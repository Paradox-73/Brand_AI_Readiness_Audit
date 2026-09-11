# JSON-LD templates

Fill-in templates for every type this skill checks. `check.py` pre-fills these with values
already extracted from the site being audited — real brand name, real URL, real logo, real
`sameAs` targets, real prices, real FAQ questions taken from the page's own H2s — so the
snippet in the report is ready to paste rather than ready to research.

Placeholders left as `<angle brackets>` are the ones the audit genuinely could not determine.

**So every fact about a business is in angle brackets in the templates below.** A founding
date, a telephone number, a street address, a price, a publication date and an answer to an
FAQ are facts only the site owner has. Printing plausible-looking ones here would hand a
reader following the convention above a block of fabricated facts to publish as data — which
is the exact failure this skill reports at `high` when it finds markup disagreeing with the
page. Only `https://example.com/`, a domain reserved for documentation, and schema.org's own
enumerated values are written out in full.

**Where these go.** In the `<head>`, inside `<script type="application/ld+json">`. One block
per entity. Organization belongs in the site-wide template so it appears on every page.

**Validate after pasting.** Google's Rich Results Test or `validator.schema.org`. A block
that fails to parse is discarded silently and completely, which is worse than having none,
because the site believes it has structured data and nobody notices.

---

## Organization

The most important block on any site: the one place a machine reads the brand's identity as
data rather than inferring it from marketing copy.

**Where `logo` may come from.** It is the field a knowledge panel renders as the brand mark,
so a wrong value is visible to every user of every assistant that reads the markup. A museum's
block carried the og:image of its `/events` page — a 2.1 MB photograph of a talk — because the
reader took the first og:image on any page. An og:image is a social card: on every page but
the front door it pictures the story, not the publisher. Three sources qualify, in this order:

1. a `logo` declared in the site's own identity markup, which is the site saying outright
   that this is its logo;
2. the homepage's own og:image, the one page whose social card is about the publisher;
3. an image whose own filename calls it a logo and which the site puts on more than one page,
   so it is a template asset rather than one illustration inside one article.

Nothing else. Where none of the three exists the snippet carries an admitted placeholder: a
placeholder costs the owner a minute, and a confident wrong URL gets published.

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "<Legal or trading name, written exactly as everywhere else>",
  "alternateName": "<Other forms people use, if any>",
  "url": "https://example.com/",
  "logo": "https://example.com/logo.png",
  "description": "<Brand> is a <category> that <does what> for <whom>.",
  "foundingDate": "<YYYY-MM-DD, or the year alone if that is all you publish>",
  "email": "<the address you publish for enquiries>",
  "telephone": "<the number shown on the site, in full international form>",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "<street address as printed on the contact page>",
    "addressLocality": "<town or city>",
    "addressRegion": "<state, province or county, where used>",
    "postalCode": "<postal or ZIP code>",
    "addressCountry": "<ISO 3166-1 two-letter country code>"
  },
  "sameAs": [
    "https://www.linkedin.com/company/<you>",
    "https://www.wikidata.org/wiki/<Qnnnn>",
    "https://www.crunchbase.com/organization/<you>",
    "https://github.com/<you>"
  ]
}
</script>
```

Three fields carry most of the value:

- **`description`** must be the *same sentence*, word for word, as the one on your homepage,
  your LinkedIn page and your press kit. Identical wording across independent sources is the
  strongest corroboration signal available (mechanism D). Rewriting it per channel throws
  that away.

  It must also still be true next quarter. This block sits in the site-wide template and is
  read as your identity until somebody edits it, so a sentence naming a version, a release, a
  date or an event does not belong in it: one open-source project's block was filled with its
  homepage's top news item and told every assistant that the organisation *is* a point
  release. A founding year is fine — "since 1934" is as true next year as this one. What is
  not fine is a sentence pinned to a moment.

  Whole sentences, and only sentences. A description scraped out of a page picks up whatever
  sits in the same run of visible text and stops wherever the reader that captured it stopped:
  one national archive's block read `"En Español The National Records Office is the nation's
  record keeper. … available to you, whether"`, opening on a language-switcher link label and
  ending mid-clause. Cut a leading menu label, drop a trailing clause that never reaches a full
  stop, and leave a deliberate one-line tagline as it is.
- **`logo`** is what a knowledge panel renders beside your name, so a wrong URL here is
  visible to every user of every assistant that reads this block. It must be an image you
  publish as your mark. Do not reach for an og:image from an inner page: that tag is the
  picture *that page* wants shown when it is shared, and one museum's block ended up
  publishing a 2.1 MB photograph from its events page as the institution's logo. Your
  homepage's own og:image is a reasonable source; the file your template puts in the header is
  a better one. If you have neither to hand, leave the placeholder in and fill it later.
- **`sameAs`** is how a machine confirms the company on this site is the company on those
  profiles. Under three entries and the brand is close to a single unlinked claim; six or more
  is the level typical of brands assistants name, and breadth across platforms is what
  `freshness-corroboration-audit` measures, not prestige.

Use `LocalBusiness` (or `Store`, `Restaurant`, `ProfessionalService`) instead of
`Organization` when the business has premises customers visit, and add `openingHoursSpecification`
and `geo`.

`LocalBusiness` is schema.org's type for a single storefront, clinic or restaurant, so it is
a narrowing claim that has to be earned. "Somewhere you can walk into" is one question, and
`site_kind` in `audit_common.py` answers it once, from the whole structure of the site;
`_identity_type` reads that answer rather than keeping a second, narrower rule of its own.
A library with opening hours met that older rule, which is how an institution whose address
suffix a registry controls was handed a site-wide block declaring the entire body to be one
storefront. `Organization` is the safe answer, true of all of them.

A site with several places that do not trade — campuses, depots, field offices — still needs
a block per place, because a page printing a street address and declaring no place markup
cannot answer "where" for a machine. What it does not need is trading hours it does not keep:

```json
{
  "@context": "https://schema.org",
  "@type": "Place",
  "name": "<brand> - <place name>",
  "url": "https://example.com/places/<place>",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "<this place's street address>",
    "postalCode": "<this place's postal code>"
  },
  "parentOrganization": { "@type": "Organization", "name": "<brand>" }
}
```

Add `telephone` and `openingHoursSpecification` to that block only where the place really has
its own line and its own hours. Invented hours are worse than none: they are a fact a machine
will repeat.

**An address is not a branch until it is a place, and this site's place.** Reading addresses
off the page answers "does this page print an address no other page prints" and nothing else,
and both of the things it cannot answer were being assumed. A volunteer non-profit was told,
at high confidence, that three of its pages "print their own street address … each of those
addresses is different from the others, which is what a business with separate branches looks
like". The three were the organisation's PO Box, an address inside a book review — which is
the book author's — and a directory of freelance consultants listing four different people's
towns. The block offered would have had a charity publish other people's addresses as its own
locations, which is the defect this file's own subject-of-the-node rule exists to prevent.

Two separable tests, both required before a page counts:

- **A post-office box is not a place.** It is a delivery point at a sorting office and
  nobody visits it, so it never counts towards "this site has separate places" and is never
  published as a `LocalBusiness`. The street patterns have no notion of one and do not need
  it for their own purpose: `PO Box 1290, <town> <code>` matches because the box number reads
  as a house number and the town and code follow it exactly as they follow a street. It is a
  postal address. It is not a place. The mark has to introduce the address rather than merely
  appear on the page — a page that prints a PO Box on one line and a real street on another
  says both.
- **An address on a page about somebody else is theirs.** It counts as this site's on a page
  the site publishes about its own places — a location, contact, home or about page — or
  where the words around it are in the first person or name the brand, which is what
  `speaks_about_itself` in `audit_common.py` decides and what already refuses "The
  <Something> Foundation" naming another organisation. A branch page printing a bare address
  in an `<address>` element is the commonest branch page there is and passes on the first;
  a book review, a supplier directory and a partner page pass on neither.

---

## Product and Offer

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "<Product name as shown on the page>",
  "description": "<One sentence: what it is and who it is for>",
  "image": "https://example.com/<the product image the page already shows>",
  "sku": "<your SKU>",
  "brand": { "@type": "Brand", "name": "<Brand>" },
  "offers": {
    "@type": "Offer",
    "url": "https://example.com/<this product's own URL>",
    "price": "<the price shown on the page, digits and a decimal point only>",
    "priceCurrency": "<ISO 4217 code, such as GBP or USD>",
    "availability": "https://schema.org/InStock",
    "priceValidUntil": "<YYYY-MM-DD, only if you genuinely hold the price until then>"
  }
}
</script>
```

- `price` is a **plain number**: no currency symbol, no thousands separator. The symbol goes
  in `priceCurrency` as an ISO 4217 code.
- `availability` must be a schema.org URL: `InStock`, `OutOfStock`, `PreOrder`,
  `BackOrder`, `Discontinued`.
- **Bind these to the same template variables that render the visible price.** Hand-written
  markup drifts from the page within weeks, and a wrong price asserted as data is worse than
  no price at all.

For a price range, use `AggregateOffer` rather than picking one:

```json
"offers": {
  "@type": "AggregateOffer",
  "lowPrice": "<cheapest variant's price>",
  "highPrice": "<dearest variant's price>",
  "priceCurrency": "<ISO 4217 code>",
  "offerCount": "<how many variants that range covers>"
}
```

---

## Article

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "<The H1, verbatim>",
  "description": "<One sentence summary>",
  "datePublished": "<YYYY-MM-DD the article first went live>",
  "dateModified": "<YYYY-MM-DD it last genuinely changed>",
  "author": {
    "@type": "Person",
    "name": "<Author name>",
    "url": "https://example.com/authors/<author>"
  },
  "publisher": {
    "@type": "Organization",
    "name": "<Brand>",
    "logo": { "@type": "ImageObject", "url": "https://example.com/<logo file>" }
  },
  "mainEntityOfPage": "https://example.com/<this article's own URL>"
}
</script>
```

**`dateModified` must move only when the content actually changed.** Bumping it on every
build asserts a freshness that is not real, and consumers that notice the date always
changing learn to discount it — costing you the signal you were trying to fake.

---

## FAQPage

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "<a question the page actually asks, worded as a person would ask it>",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "<the answer the page already gives, copied from the page>"
      }
    }
  ]
}
</script>
```

- **The answer text must match the page.** A mismatch is treated as spam by several
  consumers, and the penalty is worse than the omission.
- **Phrase questions the way a person asks an assistant** — "How much does X cost?", not
  "Pricing information". The whole value of this type is that it is already shaped like the
  thing an assistant is trying to produce.
- **Every question needs an answer under it on that page, or it is not a question this page
  answers.** A media site's games page carries the titles of three videos it links to, each
  one written as a question and each one a heading. Nothing on the page answers any of them —
  the answers are in the videos. It was reported as "1 page of questions and answers has no
  FAQPage markup", ranked second in what to fix first, and handed an `FAQPage` block whose
  three `acceptedAnswer` values had no source anywhere on the page. That is invalid markup of
  the kind a search engine demotes a site for.

  Two guards existed and neither could see it. The link-title test compares a heading against
  the anchor text the crawl recorded, and the crawl keeps one entry per destination URL with
  the first anchor's text: a card whose thumbnail image links to the video contributes an
  empty text and swallows the title anchor, so the question was never in the anchor set. The
  site-furniture test asks whether the template prints the heading everywhere, and three video
  titles on one page are that page's own content.

  Both of those ask where a heading came from. The question they stand in for is whether the
  page answers it, and that one is answerable directly, from two sources: the paragraph the
  crawl recorded under the heading, which it only keeps at 40 characters or more, and the
  words the page prints between that heading and the next one — which is what covers a
  question-and-answer block written with H3s, where the first source has nothing. A question
  with nothing under it is one this page asks and another page answers.

  This applies to a page already typed `faq` as well. That type comes from four
  question-shaped H2s or from the page's own address, and a listing of linked titles
  satisfies both. The one page that qualifies without an answered heading is one that carries
  no question heading at all and still calls itself an FAQ — a `<dl><dt>question<dd>answer`
  list, which a heading reader cannot see and the site's own label can.

---

## BreadcrumbList

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com/" },
    { "@type": "ListItem", "position": 2, "name": "<the section this page sits in>", "item": "https://example.com/<that section>" },
    { "@type": "ListItem", "position": 3, "name": "<this page's own title>", "item": "https://example.com/<this page>" }
  ]
}
</script>
```

Mirror the visible trail exactly. If there is no visible breadcrumb, add one — it helps
visitors as much as machines, and `engagement-audit` checks for the visible version
separately.

---

## WebSite with SearchAction

Only if the site genuinely has on-site search. Declaring a capability that does not exist is
worse than declaring nothing.

The target must be on the site's own origin. A `SearchAction` says "this is where *my* search
runs", so a form that submits to a general web-search engine is not on-site search and its
results URL is not a target: two sites whose header search box is a third-party web-search
form were handed `"target": "https://<a search engine>/search?q={search_term_string}"`, which
no consumer can use and which asserts an index the site does not own. If the only search box
on the site posts to another host, the site has no on-site search index — say that instead.

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "url": "https://example.com/",
  "name": "<Brand>",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://example.com/<the search form's own action>?<the name of its input>={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}
</script>
```

`{search_term_string}` is schema.org's own token and is written verbatim. Everything around it
comes off the site's search form — its `action` and the `name` of the box people type into are
the whole of the target. `search?q=` is the common shape, not a universal one, and guessing it
declares a search URL that may return nothing.

---

## Combining blocks with `@graph`

Several entities in one block, cross-referenced by `@id`. Cleaner than repeating the
Organization object inside every other type:

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    { "@type": "Organization", "@id": "https://example.com/#org", "name": "<Brand>", "url": "https://example.com/" },
    { "@type": "WebSite", "url": "https://example.com/", "publisher": { "@id": "https://example.com/#org" } }
  ]
}
</script>
```

The parser in this marketplace flattens `@graph` before checking types, so either form is
read correctly.

---

## The four mistakes worth checking for

1. **A trailing comma.** The commonest cause of an unparseable block, and it discards
   everything.
2. **An unescaped quote inside a description.** Usually a template interpolating a raw value
   rather than a JSON-escaped one.
3. **A template variable that rendered empty**, leaving `"price": ""`, which is invalid
   rather than absent.
4. **Markup that disagrees with the page** — the most damaging of the four, because nothing
   visibly breaks and the wrong fact gets repeated confidently.
