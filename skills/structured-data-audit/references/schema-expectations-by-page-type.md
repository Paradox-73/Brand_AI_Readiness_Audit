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

**5. Content fallback** — four or more question-shaped H2s → `faq`. Otherwise `other`.

---

## What is expected of each type

| Page type | Expected | Severity if absent | Never expected |
|---|---|---|---|
| **home** | Organization or LocalBusiness; WebSite + SearchAction *if the site has search* | high / low | Product, Article, author |
| **about** | Organization (site-wide is fine) | high | Product, Offer, dates |
| **contact** | Organization with address and telephone | high | Product, Article |
| **product** | Product + complete Offer; BreadcrumbList | high / medium | Article, author, dates |
| **category** | BreadcrumbList | low | Product, Offer, dates |
| **pricing** | prices in text; Offer or Product optional | — | Article, author |
| **service** | Service or Organization; BreadcrumbList | low | Product, Offer, dates |
| **article** | Article with datePublished, dateModified, author; BreadcrumbList | medium | Product, Offer, price |
| **faq** | FAQPage | medium | Product, Offer, dates |
| **location** | LocalBusiness with address, telephone, openingHours | high | Product, Article |
| **comparison** | Article or Organization | — | Product, Offer |
| **press** | Organization | — | Product, Offer |
| **careers** | — | — | anything |
| **legal** | — | — | **anything at all** |

Two rows carry most of the weight:

**`legal` expects nothing, ever.** Privacy policies and terms pages are excluded from
`CONTENT_TYPES` entirely — no schema check, no CTA check, no freshness check, no metadata
check. A terms page with no Organization markup, no call to action and a 2019 date is a
normal terms page. Reporting it would be pure noise, and the volume of it would drown the
real findings.

**`careers` expects nothing.** Job listing pages have their own vocabulary (`JobPosting`)
that is outside this audit's scope, and asking a careers page for Product markup is absurd.

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
| Product | `name`, `description`, `image`, `brand` | `brand`, for disambiguation |
| Offer | `price`, `priceCurrency`, `availability` | all three; a Product with no priced Offer answers "what is this" but not "what does it cost" |
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
