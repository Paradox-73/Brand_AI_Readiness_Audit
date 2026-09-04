---
name: fact-extractability-audit
description: Check whether an assistant fetching a page could find a sentence worth quoting. Looks for a one-line definition of what the brand actually is, tests whether sections answer first or open with warm-up prose, confirms that price, location, contact method and founding facts are stated in plain text somewhere, and flags naming inconsistency, pronoun-only introductions and sentences too long to excerpt. Use when a brand is technically crawlable and correctly marked up but assistants still describe it vaguely, generically, in a competitor's words, or not at all. This is the check most audits miss.
license: MIT
compatibility: Requires Python 3.10+ with beautifulsoup4 and lxml. Makes no network requests.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "B C D"
  gate: "quotable facts"
---

# Fact extractability audit (gate C: can a machine quote a clear fact?)

## When to use

Run after structured data. A site can pass every previous gate — crawlable, readable,
correctly marked up — and still lose, because being *parseable* is not the same as being
*quotable*. Assistants lift short passages and stand behind them. A page full of confident
prose that never states a fact gives them nothing to lift.

This is the skill to reach for when someone says "we rank fine but the assistant describes
us as 'a company that provides solutions'", or when a competitor's words get used to
describe your brand.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.

## Procedure

1. **The entity definition. Start here; it is the single most valuable sentence on any
   site.** Search the first 150 words plus the opening headings of the homepage and about
   page for a sentence of the form `<Brand> is a <category> that <does what> for <whom>` —
   the brand name as grammatical subject, a copular verb, and at least 20 characters of
   actual predicate. "Acme is a company." is technically a definition and tells nobody
   anything.

   Absence is **high**. Distinguish two cases, because the fixes differ:
   - the brand is named but never defined;
   - the opening uses "we" and "our" three or more times and **never states the brand name at
     all** — a machine reading that page cannot tell whose site it is.

   When a definition *is* found, quote it back in the not-applicable reason. Showing the
   sentence that works is as useful as flagging its absence.

2. **Heading structure.** Exactly one H1 per page, naming the subject rather than a slogan;
   no skipped levels. Zero or multiple H1s is **medium**, a skipped level **low**. Headings
   are how a machine works out which part of a long page answers which question.

3. **Slogan headings.** Compare each H2's content words against the whole page's text. A
   heading using vocabulary the page never uses again ("Built to fly", "Dream bigger") is a
   slogan. Report only when **over 80%** of at least five judged headings are disconnected,
   and skip headings made entirely of short or common words ("Who it is for") — they carry no
   vocabulary to match and are not evidence either way. The bar is deliberately high: a good
   heading often does not repeat itself in its own first sentence, and "Starter plan"
   followed by "$480 per month" is correct writing, not a defect.

4. **Answer-first sections.** On pages a visitor arrives at with a specific question —
   pricing, FAQ, product, service, location, comparison — check whether the first sentence
   under each H2 contains a number, a currency amount, a date, or a definition. Report the
   share that are "fluff-first" when it exceeds half, across three or more sections.
   **Exclude navigational sections** ("Related", "Read next", "Where to go next") and any
   section that is over 70% link text: judging a related-links block for not opening with a
   number would penalise exactly the wayfinding this marketplace recommends elsewhere.

5. **The four core facts.** Each is either found — with the URL *and the sentence that
   states it* — or missing. A fact recorded as found without a quotable sentence is not
   found: "feel free to call" contains "free" and was read as a stated price, "practice
   areas" as a stated service area, and each false pass silently removed the true finding
   underneath it. When the check passes, print the sentences in the not-applicable reason,
   so a coincidence of vocabulary is visible in the report instead of hidden behind a pass.
   - **Price**, or an explicit "contact us for pricing" statement. Missing on a site that
     *has* a pricing page is **high**; missing entirely is **medium**. An explicit refusal to
     publish prices is quotable; silence is not.
   - **Location or service area.** A postal address, or a statement of where the business
     operates. Missing on a site with LocalBusiness signals or location pages is **high**.
   - **Contact method.** An email or phone number in text, not only behind a form.
   - **Founding or team facts.** A founding year, or named leadership. These are what
     distinguish this brand from another with a similar name.

6. **Ambiguity traps.**
   - **Naming consistency.** Compare only names the site *asserts* as its identity —
     `Organization.name` and `og:site_name`. Never compare names guessed from a `<title>`:
     "Brand | Tagline" splits into a brand and a tagline, and treating the tagline as a
     variant would fire this finding on almost every site on the web. A second name already
     declared in `alternateName` is not a contradiction; the site has said the two are one
     entity.
   - **Jargon density.** Over 25% of sentences exceeding 30 words is **low**. A quotable fact
     must survive being lifted out of its paragraph, and a 40-word sentence carrying three
     qualifications cannot be excerpted without changing its meaning.

7. **Do not double-report.** Facts locked in images, PDFs or video belong to
   `render-readability-audit`. This skill only asks whether the fact is stated in text.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), and signals `entity_definition_found`, `entity_definition`,
`fluff_first`, `fluff_first_share`, `core_facts_found`, `core_facts_missing`.

Findings carry mechanism `B` (mostly), `C` (heading structure) or `D` (naming), and a
`root_cause` of `no-entity-definition`, `heading-structure`, `fluff-first`,
`missing-core-fact`, `name-inconsistency` or `long-sentences`.

Fixes are rewrite templates, not advice: the exact sentence shape to use with the brand name
already filled in, an answer-first section pattern, and an FAQ skeleton built from the site's
own H2s.

## Not applicable when

- **The site is not in English.** Three of the six checks here reason about English sentences: the definition pattern, the answer-first test and the sentence-length threshold. On a site declaring another language they decline and say so, rather than guessing. Heading order, core facts and name consistency are structural and still run.

- No brand name could be determined from structured data, `og:site_name` or the page title —
  there is nothing to look for a definition of.
- Neither a home nor an about page was crawled.
- The site declares its name in only one authoritative place — there is nothing to disagree
  with, and title-derived guesses are deliberately not compared.
- No page has five or more H2 sections (slogan check) or three or more (answer-first check) —
  the share would be noise.
- No page has eight or more sentences — sentence length cannot be measured meaningfully.
- A core fact is present: the report names the page it was found on.

## References

- `references/answerability-rules.md` — the sentence patterns an assistant can and cannot
  lift, with worked before-and-after rewrites for each core fact.
