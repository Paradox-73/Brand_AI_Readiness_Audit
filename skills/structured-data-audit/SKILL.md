---
name: structured-data-audit
description: Check whether a site states its facts in a form a machine can read without interpretation. Audits JSON-LD presence, validity and completeness keyed to each page's detected type, verifies that the markup agrees with the visible page, and checks title, meta description, Open Graph and lang hygiene. Generates paste-ready Organization, Product, Article and FAQPage snippets pre-filled with values already found on the site. Use when an assistant describes a brand vaguely or with the wrong price, when rich results never appear, or as the structured-data gate of a full AI-readiness audit. Never asks for Product markup on a site that sells nothing.
license: MIT
compatibility: Requires Python 3.10+ with beautifulsoup4 and lxml. Makes no network requests.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "C D"
  gate: "machine-readable facts"
---

# Structured data audit (gate C: are the facts machine-readable?)

## When to use

Run after the readability gate. Prose can be parsed but must be interpreted; structured data
is an assertion by the site owner that needs no interpretation at all. This skill checks
whether those assertions exist, parse, are complete, and are *true of the page they sit on*.

Reach for it on its own when an assistant describes a brand in vague terms, quotes a stale
or wrong price, or attributes the brand's products to someone else.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.

## Procedure

**Page type first, always.** Every expectation below is keyed to the type the crawl detected
(home, about, contact, pricing, product, category, service, article, faq, location,
comparison, press, careers, legal) — which is what stops the skill demanding Product schema
from a consultancy. **And that type is a candidate, not a verdict:** "3 of 3 product pages have
no Product markup" was said about a gift category, a best-sellers list and a search-results
address. Ask the page again before grading it, and let the Product, Article and FAQ checks
share that one answer, naming every page it turns away. See
`references/schema-expectations-by-page-type.md`.

Eighteen checks are registered; they are grouped here into ten questions. Each line is the
question and the worst severity it can reach. `references/markup-checks.md` lists every
registered check id with what it reads, its threshold and where that number came from, the
severity it can reach, and the real report behind every guard.

1. **Validity before absence.** A block that fails to parse is **high**: invalid JSON-LD is
   discarded entirely, so the site paid for structured data and gets none of the benefit while
   the block is still visibly there. Where a site publishes *none* of it — no JSON-LD, no
   Microdata, no RDFa, nothing broken — checks 2 to 7 fold into **one** finding rather than six.
2. **Organization-level identity.** No `Organization`, `LocalBusiness` or subtype anywhere is
   **medium**, not high: 62% of the brands assistants name have none either. Missing
   high-value properties (`name`, `url`, `logo`, `description`, `sameAs`) are **medium**. But
   an identity value that is *wrong* — a `name` that is really the page's `<title>`, a `url`
   pointing at a section rather than the site root — is **high**, because a consumer treats
   that field as ground truth about the company. Earn `LocalBusiness` from a visitable type,
   opening hours or location pages — never from the fact that an address appears somewhere,
   which nearly every organisation publishes for legal reasons.
3. **One LocalBusiness per branch**, when three or more pages each print a street address no
   other page prints. **High** at five or more, otherwise **medium**. Read the addresses off the
   pages: gating on the place markup the chain is missing means the defect hides itself.
4. **Product and Offer**, only where product detail pages were detected. Missing `Product` is
   **medium**; present with no `offers`, or `offers` missing `price`, `priceCurrency` or
   `availability`, is **medium** — it answers "what is this" but not "what does it cost". A
   price stated inside a nested `priceSpecification` is present and valid, and the fix adds
   what is missing beside it rather than flattening it. A page typed as a product that turns
   out to be a shelf is asked for `ItemList` instead, at **low**, and never for both.
5. **Article**, only where article pages were detected. Missing is **medium**; missing
   `datePublished`, `dateModified`, `headline` or `author` is **medium**. An index of posts is
   not a post, and the URL never settles which one a page is.
6. **FAQPage**, only where *most* of a page's headings are questions. **Medium**. The snippet
   carries only headings that really are questions, each with an answer under it, and never one
   the whole site's template repeats.
7. **BreadcrumbList** on deep pages, and **WebSite + SearchAction** on the homepage *only if
   the site actually has search*. Both **low**. Take the target from the form's `action` and the
   name of the box people type words into — not a `<select>`, and not a form that submits to
   another host.
8. **Agreement with the visible page.** The check most audits skip and the one that matters
   most: a missing fact leaves a machine uncertain, a contradictory one teaches it something
   false. An `Offer` price not among the prices on the page, or an `Organization` name appearing
   nowhere in its title, headings or text, is **high**. A `Brand` node spelling the site's own
   name differently is markup disagreeing with markup: **medium**.
9. **Metadata hygiene**, over every page the crawl fetched and not only the content-typed ones.
   Missing or duplicated `<title>`, or a meta description missing on a quarter of pages, is
   **medium**; Open Graph over 40% of pages, `lang`, and Microdata with no JSON-LD are **low**.
   A `<title>` that *is* the page's own address weighs the same as no title, and a tag whose
   whole content is punctuation - `##` - weighs the same as an absent one, in any script. The
   homepage title is graded separately and must name the business, not a promotion. A value
   still carrying `&amp;` after parsing was escaped twice by the template: its own finding,
   **medium**, owner *developer* - the copy is right and one filter is wrong, so telling a
   content owner to rewrite it would be the wrong instruction.
10. **Generate the fix, do not describe it.** Every snippet is pre-filled with values already
    extracted from the site — real name, real URL, real logo, real `sameAs`, real prices, real
    questions off the page's own H2s. A snippet the owner can paste beats "add structured data".

Five rules govern all ten, and are what to carry over when working through this by hand.

- **Three notations, one grader.** JSON-LD, Microdata and RDFa state the same things, and a
  site that states a fact in any of them has stated it. Reading JSON-LD alone told a shop whose
  theme emits `itemprop="price"` that its markup omits the price, the availability and the SKU,
  all three on the page. Open Graph is not RDFa and is never folded into an entity.
- **A value about the brand comes off a page about the brand.** Read a whole-brand claim from
  home, about or contact and nowhere else. A restaurant's `LocalBusiness` description came out
  as a tote bag's marketing copy, from a product page reached in URL order.
- **And off a node that speaks for the brand.** Flattening nested JSON-LD makes a node the page
  declared about somebody else indistinguishable from one it declared about itself: an arena's
  address, lifted from an Event's `location`, was installed as a marketplace's head office.
  `publisher` and `contactPoint` speak for the brand; `location`, `performer`, `author` and
  `brand` do not.
- **A description is pasteable or it is not published.** It starts where a sentence starts and
  ends where one ends, and it has to outlive the page it was read off — a release announcement
  is true this month and wrong next quarter, while "since 1934" is as true next year. The
  sentence itself comes from `defining_sentence` in `audit_common.py`, the one function
  `fact-extractability-audit` also calls, so the two skills cannot give one site two identities.
- **A logo comes from something the site presents as its logo.** `logo` is what a knowledge
  panel renders as the brand mark, and an `og:image` is a social card. Where no qualifying
  source exists the snippet carries an admitted placeholder.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), and signals `has_org_schema`, `has_faq_schema`,
`page_types_seen`, `article_pages`, `articles_without_author`, `pages_with_unmarked_qa`,
`index_pages_not_graded_as_articles`, `pages_not_graded_as_product_pages` and
`search_result_pages_not_graded` (the three ways a page fails to earn the type it was given,
each naming the pages and the test that turned them away),
`search_box_queries_another_site` (the page whose search form submits to another host, which
is the site saying it has no search index of its own) and
`pages_not_judged_as_truncated_at_the_read_cap` (up to ten pages the crawl stopped reading at
its size cap, set aside from every check because their markup may sit past the cut).

Findings carry mechanism `C` (or `D` for date and corroboration properties, `B` for metadata
that gets quoted) and a `root_cause` of `invalid-jsonld`, `no-org-schema`,
`no-product-schema`, `no-article-schema`, `no-faq-schema`, `missing-schema-props`,
`schema-text-mismatch`, `no-breadcrumb-markup`, `no-website-schema`,
`open-graph-incomplete`, `missing-lang`, `microdata-only`, `meta-hygiene` or
`template-escapes-values-twice`.

A finding here that says something is **missing** carries `checked[]`, the independent
sources it consulted and found empty. One source caps it at medium confidence and the report
says so. Working through this skill by hand, do the same: say where you looked, and look in a
second place before stating at high confidence that something is not there. Every false
positive this marketplace has made on a real site was one detector with a finite list of
shapes, reported as a fact about the site.

## Not applicable when

- No product detail pages were detected — Product and Offer markup is not expected. Where
  pages were *called* product pages and none earned it, the decline says that instead and
  names each page and the test that turned it away.
- No article, FAQ or location pages were detected, and no other page carries a
  question-and-answer block — same, per type. An unmarked FAQ on an ordinary page is FAQ
  content; the report used to say none was detected and then recommend writing one.
- No two crawled addresses delivered the same main text, **and** no page was reached at a
  second address differing only in its query parameters, or those twins carry different titles
  and so are different pages. Two independent detectors, and the reason names both: one
  compares page bodies across entirely different paths, the other compares query twins on one
  path.
- The site has no on-site search — SearchAction would describe a capability that does not
  exist. A same-origin link to `/search`, or a form whose action carries `?q=` or `?s=`, is
  on-site search too. A form submitting to another host is the reverse case: say the site has
  no on-site search index rather than handing it another site's search URL to declare.
- **The place advice is worded for what the site is, and never withheld.** `site_kind` in
  `audit_common.py` answers "is this somewhere you can walk into" once, and both the identity
  type and the per-place fix read that one answer — so a site whose places do not trade is
  asked for a `Place` block with an address rather than a `LocalBusiness` block with a
  telephone and opening hours per branch. The finding itself is never suppressed: a page
  printing its own street address and declaring no place markup cannot answer "where" for a
  machine whoever owns it.
- Fewer than three deep pages were crawled, or half or more of them already declare
  `BreadcrumbList` — breadcrumb markup would not change how the site is understood.
- No content page returned HTTP 200.

`references/markup-checks.md` gives the wording each of these reasons has to reach, and the
real report that made each one necessary.

## References

- `references/markup-checks.md` — every one of the eighteen registered check ids, and the
  five rules that govern all of them: what each reads, its threshold and where that number
  came from, the severity it can reach, every guard, and the real report behind it.
- `references/schema-expectations-by-page-type.md` — the expectation table, the reasoning for
  each row, and the guards on page type, `Brand` nodes, homepage titles and `lang` examples.
- `references/jsonld-templates.md` — fill-in templates for every type this skill checks,
  with the properties that matter most marked.
