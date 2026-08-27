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
comparison, press, careers, legal). This is what stops the skill demanding Product schema
from a consultancy or an author byline from a pricing page. See
`references/schema-expectations-by-page-type.md`.

1. **Validity before absence.** Any `application/ld+json` block that fails to parse is
   **high**: invalid JSON-LD is discarded entirely, so the site has paid for structured data
   and receives none of the benefit, and nobody notices because the block is still visibly
   there. Report the parser's exact message and an excerpt.

2. **Organization-level identity.** Look across every crawled page for `Organization`,
   `LocalBusiness` or a subtype. None anywhere, when a home, about or contact page was
   crawled, is **high** — this is the only place a machine reads the brand's identity as data
   rather than inferring it from marketing copy. If present, check the high-value properties:
   `name`, `url`, `logo`, `description`, `sameAs`. Missing ones are **medium**.

3. **Product and Offer**, only if product detail pages were detected. Missing `Product` is
   **high**. Present but with no `offers`, or an `offers` object missing `price`,
   `priceCurrency` or `availability`, is **medium** — it answers "what is this" but not
   "what does it cost", which is the question being asked.

4. **Article**, only if article pages were detected. Missing is **medium**; present but
   lacking `datePublished`, `dateModified`, `headline` or `author` is **medium**, because
   date and author are what let a consumer decide whether to trust the writing.

5. **FAQPage**, only on pages that read as an FAQ. **Medium**: question-and-answer pairs are
   already shaped like the thing an assistant is trying to produce.

6. **BreadcrumbList** on deep pages, and **WebSite + SearchAction** on the homepage *only if
   the site actually has search*. Both **low**. Declaring a SearchAction for search that does
   not exist would be worse than declaring nothing.

7. **Agreement with the visible page.** This is the check most audits skip and the one that
   matters most. A missing fact leaves a machine uncertain; a **contradictory** one teaches
   it something false with the confidence that structured data carries. Report **high** when:
   - an `Offer` price is not among the prices shown on the same page — compared
     **numerically**, so `480.00` and `$480` are correctly treated as the same price;
   - an `Organization` name appears nowhere in the page's title, headings or body text.

8. **Metadata hygiene, last.** Missing or duplicated `<title>` and `meta description` are
   **medium** (duplicates usually mean a template dropped its page variable, so fix the
   template). Title length is only reported when 40% or more of pages fall outside 15–75
   characters — one title a few characters long is not a defect. Missing Open Graph tags are
   **low**, and only when they affect 40%+ of pages. Missing `lang` is **low**. Microdata
   with no JSON-LD is **low**: valid, but it breaks whenever the template changes.

9. **Generate the fix, do not describe it.** Every snippet in the report is pre-filled with
   values already extracted from the site — real brand name, real URL, real logo, real
   `sameAs` targets, real prices, real FAQ questions taken from the page's own H2s. A
   snippet the owner can paste beats an instruction to "add structured data".

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), and signals `has_org_schema`, `has_faq_schema`,
`page_types_seen`, `article_pages`, `articles_without_author`.

Findings carry mechanism `C` (or `D` for date and corroboration properties, `B` for metadata
that gets quoted) and a `root_cause` of `invalid-jsonld`, `no-org-schema`,
`no-product-schema`, `no-article-schema`, `missing-schema-props`, `schema-text-mismatch`,
`no-breadcrumbs` or `meta-hygiene`.

## Not applicable when

- No product detail pages were detected — Product and Offer markup is not expected, and the
  report says so explicitly rather than staying silent.
- No article, FAQ or location pages were detected — same, per type.
- The site has no on-site search — SearchAction would describe a capability that does not
  exist.
- Fewer than three deep pages were crawled — breadcrumb markup would not meaningfully change
  how the site is understood.
- Half or more of the deep pages already declare `BreadcrumbList`.
- No content page returned HTTP 200.

## References

- `references/schema-expectations-by-page-type.md` — the expectation table, and the
  reasoning for each row.
- `references/jsonld-templates.md` — fill-in templates for every type this skill checks,
  with the properties that matter most marked.
