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

Ten checks are registered; they are grouped here into eight questions, and two of the eight
are cross-cutting rules rather than checks. Each line is the question and the worst severity
it can reach. `references/extractability-checks.md` lists every registered check id with what
it reads, its threshold and where that number came from, the severity it can reach, and the
real report behind every exclusion.

1. **The entity definition.** The single most valuable sentence on any site: `<Brand> is a
   <category> that <does what> for <whom>`. Five shapes count, because English writes it five
   ways — the copula, an appositive, a defining verb, the first person, and a `description`
   declared in markup. Named but never defined is **medium**; an opening that says "we" three
   times and never states the brand name at all is **high**. When a definition is found, quote
   it back in the pass. Where one exists somewhere but not at the top of the homepage, that is
   its own check and its own finding — **medium** on another page, **low** further down the
   homepage — and it re-reads the homepage through a second detector before firing. A line the
   site repeats on every page, such as a footer tagline, counts too: it is read one block at a
   time from before the site-wide boilerplate strip, because that strip removes exactly the
   sentence this check most wants. Found there, it is **low**.
2. **Heading structure.** Only a page with **no H1 at all**, read from both the visible
   document and the whole markup, is reported, at **medium**. Multiple H1s and skipped levels
   are measured against nothing and the pass says so.
3. **Slogan headings.** An H2 whose vocabulary the page never uses again. Reported only over
   **80%** of at least five judged headings. English-only, and a page that lists other pages is
   excluded because its headings *are* its content.
4. **Answer-first sections.** On a page a visitor arrives at with a question, does the first
   sentence under each H2 assert or defer? Over **70%** deferring, across three or more
   sections and at least four offenders, is the finding. Navigational sections are excluded.
5. **The four core facts** — price, location or service area, contact method, founding year and
   whoever is named as responsible. Each is either found *with the sentence that states it*, or
   missing. Missing on a site whose own structure implies it is **high**; missing on a site that
   never claimed it is **medium**.
6. **An absence claim has to be earned by the sample.** Below **half** of the page-shaped URLs
   this crawl could have read, a title saying "never" or "no page" instead says how many pages
   it speaks for, and the severity drops a step.
7. **Ambiguity traps.** Naming consistency, compared only across names the site *asserts* —
   never a name split out of a `<title>`. And jargon density: over **40%** of a page's sentences
   past 30 words is **low**, measured only where the page is established to hold prose.
8. **Do not double-report.** Facts locked in images, PDFs or video belong to
   `render-readability-audit`. This skill asks only whether the fact is stated in text.

Three rules govern all eight, and are what to carry over when working through this by hand.

- **A fact is found only when a sentence can be quoted for it.** "Feel free to call" was read
  as a stated price and "practice areas" as a stated service area, and each false pass silently
  removed the true finding underneath it. Test the *trimmed* sentence the report will print.
- **A fact quoted for the brand has to be about the brand.** The subject must be this site's
  owner. A charity's partners page supplied another organisation's founding year, seven years
  before the audited charity existed. But a sentence naming nobody belongs to whoever the
  sentence before it was about — "Founded in 1884, it houses …" is the brand's own fact.
- **A check may report the absence of what it can recognise, not of what it cannot.** A year
  beside a founding word is enumerable; a named person is any capitalised run in a sentence
  written a dozen ways, so only the missing year is reported and a person found lowers its
  severity.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), and signals `entity_definition_found`, `entity_definition`,
`fluff_first`, `fluff_first_share`, `core_facts_found`, `core_facts_missing` and
`pages_read_share` — the share of what this crawl could have read that it did read, which is
what decides whether a title may say "never".

Findings carry mechanism `B` (mostly), `C` (heading structure) or `D` (naming), and a
`root_cause` of `no-entity-definition`, `heading-structure`, `fluff-first`,
`missing-core-fact`, `name-inconsistency` or `long-sentences`.

Fixes are rewrite templates, not advice: the exact sentence shape to use with the brand name
already filled in, an answer-first section pattern, and an FAQ skeleton built from the site's
own H2s.

A finding here that says something is **missing** carries `checked[]`, the independent
sources it consulted and found empty. One source caps it at medium confidence and the report
says so. Working through this skill by hand, do the same: say where you looked, and look in a
second place before stating at high confidence that something is not there. Every false
positive this marketplace has made on a real site was one detector with a finite list of
shapes, reported as a fact about the site.

## Not applicable when

- **The site is not in English.** Four checks reason wholly about English sentences: the
  definition pattern, the answer-first test, the sentence-length threshold and the
  heading-vocabulary overlap. They decline and say so rather than guessing. Heading order and
  name consistency are structural and still run — and so is most of the core-facts check, but
  **not all of it**: the service-area pattern and the founding-year and named-person patterns
  are English too, so on a non-English site those facts are reported as *"not checked"*, with
  the pattern named, rather than reported absent. The address, the declared markup and the
  contact routes are read whatever the language.
- **The site declares no language and is not written in Latin letters.** Text overwhelmingly in
  Cyrillic, Greek, Han, Kana, Hangul, Arabic, Hebrew, Devanagari or Thai is not English whatever
  the markup says or fails to say. The same four checks decline, naming the script and not a
  language — Cyrillic is four languages and picking one would be an invention.
- **The site is not the kind of thing the fact belongs to.** `site_kind` in `audit_common.py`
  reads what a site is from its own structure. The founding year is asked only of something that
  could be a business, shop, organisation, publication or public body; the location only of the
  first three. What is gated is the expectation, never the observation: a fact the site *does*
  state is still read and reported whatever kind it is.
- No brand name could be determined from structured data, `og:site_name` or the page title —
  there is nothing to look for a definition of.
- Neither a home nor an about page was crawled.
- The site declares its name in only one authoritative place — there is nothing to disagree
  with, and title-derived guesses are deliberately not compared.
- No page that is not an index of other pages has five or more H2 sections (slogan check), or
  no page has three or more (answer-first check) — the share would be noise.
- No page holds prose with eight or more sentences in it — sentence length cannot be measured
  meaningfully, and a page whose words sit in list items has no sentences to measure.
- A core fact is present: the report names the page it was found on and quotes the sentence.

`references/extractability-checks.md` gives the report behind each of these gates, including the
command-line tool told to state where it serves customers and the Cyrillic library told it says
nothing about itself.

## References

- `references/extractability-checks.md` — every one of the ten registered check ids: what each
  reads, its threshold and where that number came from, the severity it can reach, the sentence
  shapes it recognises, every exclusion, and the real report behind it.
- `references/answerability-rules.md` — the sentence patterns an assistant can and cannot
  lift, with worked before-and-after rewrites for each core fact.
