# Round 2 failure modes, and where each one is detected

The Round 2 analysis named four failure modes and a monitoring approach. This file records
each one, the reasoning behind it, and the specific check in this marketplace that detects
it. Where a failure mode cannot be reliably detected from a crawl, that is said plainly
rather than papered over.

---

## 1. Invisible — the brand is not retrieved at all

**Root cause as diagnosed.** Core information is locked inside non-textual formats such as
images, or requires client-side rendering to appear. Content that is not present as plain
text in the delivered response is effectively invisible to an automated crawler during
retrieval.

**Fix as proposed.** Exhaustive Schema.org structured data defining inventory as
machine-readable entities, plus server-side rendering so crawlers receive parsed, indexable
HTML without executing JavaScript.

**Where it is detected now.**

| Symptom | Check | Skill |
|---|---|---|
| Content only exists after hydration | `spa-shell-detection`, `static-vs-rendered-text-gap` | render-readability-audit |
| Facts baked into images | `facts-locked-in-images` | render-readability-audit |
| Facts only in a PDF | `facts-locked-in-pdfs` | render-readability-audit |
| Facts only spoken in video | `video-transcripts` | render-readability-audit |
| No machine-readable entity data | `organization-markup`, `product-markup` | structured-data-audit |
| Crawler refused before any of this matters | `robots-blocks-*`, `bot-manager-*` | crawl-access-audit |

**What the audit added.** The original diagnosis assumed the crawler gets in. It often does
not: a WAF returning 403 to `ClaudeBot` is invisible in robots.txt and defeats every
rendering fix. That is why gate A is split into "let in" and "can read", and why the
bot-user-agent comparison probe exists.

---

## 2. Stale — the brand is described with outdated facts

**Root cause as diagnosed.** Assistants weigh facts by corroboration: a claim repeated
across many independent sources is treated as trustworthy. A recent rebrand exists in
isolation on the brand's own domain, while older directories, cached pages and third-party
reviews still repeat the previous logo and discontinued pricing. The model therefore trusts
the outdated *consensus* over the accurate *single source*.

This is the sharpest insight in the Round 2 work, and it inverts the usual assumption: the
brand's own site being correct is not enough, and can even be the minority position.

**Fix as proposed.** A syndication push — distribute updated machine-readable product feeds
to retail partners, review aggregators and knowledge bases such as Wikidata, so assistants
find fresh, corroborated citations across the web rather than one lonely correct page.

**Where it is detected now.**

| Symptom | Check | Skill |
|---|---|---|
| Few independent sources corroborate the brand | `authoritative-profiles` | freshness-corroboration-audit |
| No Wikidata or Wikipedia anchor | `entity-ambiguity`, `authoritative-profiles` | freshness-corroboration-audit |
| `sameAs` incomplete or absent | `organization-markup` | structured-data-audit |
| Content itself has gone stale | `content-freshness`, `sitemap-lastmod-recency` | freshness-corroboration-audit |
| No date signal to judge currency by | `date-signals-present`, `footer-copyright-year` | freshness-corroboration-audit |
| The site disagrees with **itself** | `fact-consistency-across-pages` | freshness-corroboration-audit |

**What the audit added.** Self-contradiction. If a brand's own pages disagree on the
telephone number, the address or the boilerplate, syndication propagates the disagreement.
Fixing the outside world before fixing the inside is wasted effort, so the canonical
boilerplate block is the first recommendation, not the last.

**Honest limitation.** An audit that only crawls the brand's own site cannot see what
directories and aggregators currently say. It detects the *preconditions* for stale
consensus — weak corroboration, no canonical boilerplate, no authoritative anchor — and
records a suggested web-search query, but it does not verify the wider web.

---

## 3. Bouncing — visitors arrive and leave

**Root cause as diagnosed.** A context gap. Visitors arrive with hyper-specific intent
formed during an AI conversation, and land on a generic site that treats every visitor
identically. The site is built for top-of-funnel discovery and fails entirely to orient
someone arriving mid-journey.

**Fix as proposed.** Intent-driven landing pages: read URL query parameters mapped from AI
citations and reorder the page to foreground the exact product and price the visitor was
reading about.

**Where it is detected now.**

| Symptom | Check | Skill |
|---|---|---|
| Page does not say what this is or what to do | `homepage-orientation` | engagement-audit |
| No way onward | `dead-end-pages`, `broken-internal-links` | engagement-audit |
| No way back or upward | `breadcrumb-navigation`, `consistent-site-chrome` | engagement-audit |
| Page does not deliver what the citation promised | `title-body-alignment` | engagement-audit |
| Content demanded before content given | `intrusive-interstitials`, `form-friction` | engagement-audit |
| Reachable only from the sitemap | `orphan-pages` | engagement-audit |

**Why the proposed fix is not the recommended one.** Query-parameter personalisation is
fragile in this specific context: assistants frequently cite a bare canonical URL and strip
tracking parameters, so the intent signal often never arrives. Worse, serving reordered
content by parameter risks cloaking — the crawler and the visitor see different pages, which
is the failure this whole audit exists to prevent.

The marketplace therefore recommends the durable version of the same idea: make **every**
page self-orienting, so a visitor landing anywhere is oriented without the site needing to
know how they got there. Where genuine intent segments exist, serve them as real, crawlable,
separately-addressable pages — which is the `R-USE-CASE-PAGES` and `R-COMPARISON-PAGES`
recommendation, and covers mechanism E at the same time.

---

## 4. Loss of brand voice — the brand is flattened into generic data

**Root cause as diagnosed.** Assistants distil answers into neutral summaries, stripping a
brand's personality and reducing it to commoditised specifications. Machines reliably extract
explicit factual entities and discard surrounding marketing copy, unless the subjective
framing is tied unambiguously to the factual, technical definition of the thing itself.

**Fix as proposed.** Embed proprietary terminology directly into structured data nodes and
extractable semantic HTML. If a component is technically defined with a branded name rather
than a generic description, an assistant must use the brand's own words to describe the
product accurately.

This is the most original of the four and generalises well beyond the case it came from.

**Where it sits now.** As the `R-BRAND-TERMINOLOGY` proactive recommendation, not as a
defect check.

**Why it is a recommendation and not a check.** Detecting "this brand has no distinctive
terminology" from a crawl is not reliable. A skill cannot tell a deliberately-coined
component name from an ordinary noun phrase without knowing the industry's baseline
vocabulary, and a check that guessed would produce exactly the false positives this
marketplace is built to avoid. Reporting it as a defect would mean asserting something the
evidence does not support. It is offered as an improvement instead, with the mechanism
explained, which is the honest place for it.

---

## 5. Monitoring

**As proposed.** Query major LLM APIs weekly with targeted category questions; parse outputs
for mention frequency (visibility) and exact string matches on current-year facts
(staleness); use UTM parameters on AI-cited links to track on-site retention (engagement).

**Where it sits now.** Out of scope, deliberately. This marketplace is a point-in-time audit
that reads a website; it does not run a monitoring service, hold API credentials, or persist
state between runs. Two things it does contribute to a monitoring programme:

- `citation_simulation[]` in the report gives a **baseline**: the exact sentence an assistant
  would most likely quote from each key page today. Re-running after a fix shows movement
  without needing any external API.
- `signals.suggested_web_search` records the query that would test entity ambiguity, so a
  monitoring job has a defined starting point.

The UTM caveat is worth stating: assistants often cite bare canonical URLs and strip
parameters, so UTM-based attribution of AI referrals undercounts. Referrer-based measurement
is more reliable, and neither is complete.
