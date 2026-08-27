# JSON-LD templates

Fill-in templates for every type this skill checks. `check.py` pre-fills these with values
already extracted from the site being audited — real brand name, real URL, real logo, real
`sameAs` targets, real prices, real FAQ questions taken from the page's own H2s — so the
snippet in the report is ready to paste rather than ready to research.

Placeholders left as `<angle brackets>` are the ones the audit genuinely could not determine.

**Where these go.** In the `<head>`, inside `<script type="application/ld+json">`. One block
per entity. Organization belongs in the site-wide template so it appears on every page.

**Validate after pasting.** Google's Rich Results Test or `validator.schema.org`. A block
that fails to parse is discarded silently and completely, which is worse than having none,
because the site believes it has structured data and nobody notices.

---

## Organization

The most important block on any site: the one place a machine reads the brand's identity as
data rather than inferring it from marketing copy.

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
  "foundingDate": "2019-03-11",
  "email": "hello@example.com",
  "telephone": "+1-555-0142",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "418 Harbour Street",
    "addressLocality": "Portland",
    "addressRegion": "OR",
    "postalCode": "97204",
    "addressCountry": "US"
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

Two fields carry most of the value:

- **`description`** must be the *same sentence*, word for word, as the one on your homepage,
  your LinkedIn page and your press kit. Identical wording across independent sources is the
  strongest corroboration signal available (mechanism D). Rewriting it per channel throws
  that away.
- **`sameAs`** is how a machine confirms the company on this site is the company on those
  profiles. Under three authoritative entries and the brand is close to a single unlinked
  claim.

Use `LocalBusiness` (or `Store`, `Restaurant`, `ProfessionalService`) instead of
`Organization` when the business has premises customers visit, and add `openingHoursSpecification`
and `geo`.

---

## Product and Offer

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "<Product name as shown on the page>",
  "description": "<One sentence: what it is and who it is for>",
  "image": "https://example.com/product.jpg",
  "sku": "<your SKU>",
  "brand": { "@type": "Brand", "name": "<Brand>" },
  "offers": {
    "@type": "Offer",
    "url": "https://example.com/product",
    "price": "38.00",
    "priceCurrency": "GBP",
    "availability": "https://schema.org/InStock",
    "priceValidUntil": "2027-12-31"
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
  "lowPrice": "18.00",
  "highPrice": "96.00",
  "priceCurrency": "GBP",
  "offerCount": 24
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
  "datePublished": "2026-05-14",
  "dateModified": "2026-06-30",
  "author": {
    "@type": "Person",
    "name": "<Author name>",
    "url": "https://example.com/authors/<author>"
  },
  "publisher": {
    "@type": "Organization",
    "name": "<Brand>",
    "logo": { "@type": "ImageObject", "url": "https://example.com/logo.png" }
  },
  "mainEntityOfPage": "https://example.com/blog/the-post"
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
      "name": "How much does <product> cost?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "<Product> costs $480 per month on Starter and $1,450 on Growth. There is no setup fee."
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

---

## BreadcrumbList

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com/" },
    { "@type": "ListItem", "position": 2, "name": "Ironmongery", "item": "https://example.com/products" },
    { "@type": "ListItem", "position": 3, "name": "Suffolk gate latch", "item": "https://example.com/products/gate-latch" }
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

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "url": "https://example.com/",
  "name": "<Brand>",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://example.com/search?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}
</script>
```

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
