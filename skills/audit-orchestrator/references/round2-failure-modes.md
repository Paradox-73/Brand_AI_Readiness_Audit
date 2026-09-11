# Round 2 failure modes, and where each one is detected

Two lists of failure modes, and both are answered here.

The **brief's own worked example** names seven concrete, repeatable signals that separate a
site an assistant cites from one it ignores: crawlability, JavaScript-render gaps,
missing or invalid structured data, facts locked in non-text, stale or uncorroborated facts,
entity ambiguity, and weak on-site orientation with no context retention. The first section
below maps all seven onto the checks that cover them, by check id, so a reader can confirm
the coverage without opening a `.py` file. **All seven are covered, and every one of the 96
checks this marketplace registers falls under exactly one of them.**

The **Round 2 analysis** then named four failure modes and a monitoring approach, at a coarser
grain — three symptoms a brand owner would recognise plus a fourth about how the brand is
described. Those follow, each with the reasoning behind it and the check that detects it.
Where a failure mode cannot be reliably detected from a crawl, that is said plainly rather
than papered over.

---

# The seven signals, and the check id covering each

Counts are of registered check ids — the entries that appear in `checks_run[]` and
`not_applicable[]` in every report, so a reader can tick them off against a real run. Each
check is listed once, under the signal it primarily answers; the cross-references note where
a check also bears on another signal.

## 1. Crawlability — 28 checks

Is the crawler allowed in, served a real status, and given addresses it can follow?
`crawl-access-audit` owns 27 of these; the pagination check is `render-readability-audit`'s
because it is the one crawlability failure caused by rendering.

| Check | What it settles |
|---|---|
| `robots-txt-reachable` | whether the rules file answered at all |
| `robots-txt-rules` | whether the rules could be read, and whether any line crawlers ignore is in them |
| `robots-blocks-all-crawlers` | `Disallow: /` under `User-agent: *` with no carve-out |
| `robots-blocks-ai-answer-crawlers` | search-index and live-fetch agents, the two roles that cost citations |
| `robots-blocks-ai-training-crawlers` | training agents, reported as `info` because blocking them is a rights choice |
| `robots-blocks-content-paths` | disallow rules covering what looks like real content |
| `robots-agent-groups-unambiguous` | one agent's rules stated in two groups, which no two parsers combine alike |
| `robots-rules-are-path-shaped` | a `Disallow` value that is not a path at all - a fragment of script pasted into the file - which no crawler can match and every crawler still reads |
| `robots-crawl-delay` | a delay the audit itself honours, and what it costs the crawl |
| `bot-manager-challenge-page` | a 2xx carrying a challenge script instead of the content |
| `bot-manager-user-agent-comparison` | whether the edge treats a crawler name differently, asked twice through two HTTP clients |
| `homepage-reachable` | the front door's status |
| `non-200-rate` | how much of the crawl the origin refused |
| `origin-response-time` | slow is not down, and gets its own root cause |
| `redirect-chain-length` | hops between the address published and the page served |
| `meta-refresh-redirects` | a redirect only something that renders HTML can follow |
| `noindex-on-content-pages` | pages fetched and then thrown away |
| `canonical-targets` | a canonical pointing off-domain or at a non-200 |
| `canonical-host-consistency` | one site served on two hostnames with nothing saying which is real |
| `hreflang-between-language-editions` | a site published in several languages that never tells a machine which address is which edition |
| `https-transport` | plain HTTP |
| `sitemap-present` | referenced from robots, or at either conventional location |
| `sitemap-parses` | whether it is XML at all, or the catch-all route answering |
| `sitemap-urls-resolve` | whether the addresses it advertises exist |
| `sitemap-excludes-private-paths` | per-visitor addresses spending a crawler's budget on one session |
| `unknown-paths-return-200` | what a 200 proves on this origin, run first because its answer settles three other checks |
| `llms-txt-presence` | agent-readable files, recorded and never reported as a defect |
| `crawlable-pagination` | a catalogue past the first batch with no URL to follow |

## 2. JavaScript-render gaps — 5 checks

Is the content in the first HTML response, or assembled afterwards? All in
`render-readability-audit`.

| Check | What it settles |
|---|---|
| `render-mode` | whether this site is server-rendered, client-rendered or hybrid, decided once |
| `spa-shell-detection` | a page that is a container rather than a document |
| `static-vs-rendered-text-gap` | how much text a consumer that does not run JavaScript loses |
| `thin-html` | a short page, separated from a container and from a page this audit read badly |
| `client-rendered-chrome` | navigation, header and footer that arrive only after hydration |

## 3. Missing or invalid structured data — 18 checks

Are the facts stated as data rather than left to be inferred from prose? All in
`structured-data-audit`. The last five cover the machine-readable metadata that is quoted
directly rather than parsed as an entity.

| Check | What it settles |
|---|---|
| `jsonld-validity` | a block that fails to parse, so the site paid for markup and gets none of it |
| `microdata-and-rdfa` | the two other notations, graded by the same rules |
| `organization-markup` | the one place identity is data rather than inference |
| `per-location-markup` | one `LocalBusiness` per branch on a multi-site business |
| `product-markup` | `Product`, only where product detail pages earned the type |
| `offer-completeness` | `price`, `priceCurrency`, `availability` — what it costs, not only what it is |
| `article-markup` | `Article`, its dates, its headline and its author |
| `faq-markup` | `FAQPage`, only where most of a page's headings really are questions |
| `breadcrumb-markup` | `BreadcrumbList` on deep pages |
| `website-searchaction-markup` | `WebSite` + `SearchAction`, only where on-site search exists |
| `listing-markup` | a shelf asked for the markup a shelf carries, not a product's |
| `jsonld-matches-visible-text` | markup that contradicts the page, which teaches a machine something false |
| `brand-name-in-markup` | a `Brand` node spelling the site's own name differently *(also signal 6)* |
| `canonical-declaration` | whether the site says which address is the real one |
| `title-and-description` | `<title>` and meta description presence, duplication and length |
| `homepage-title` | the front door titled after a promotion rather than the business |
| `open-graph-tags` | the card an assistant and a chat client both read |
| `html-lang-attribute` | the declaration every language-dependent reading depends on |

## 4. Facts locked where a machine cannot lift them — 13 checks

The brief names non-text formats. This marketplace treats "stated, but in a form nothing can
quote" as the same failure and covers both halves: six checks in
`render-readability-audit` for the non-text half, seven in `fact-extractability-audit` for
the unquotable-prose half.

| Check | What it settles |
|---|---|
| `unrendered-template-artefact` | a page publishing the instruction to its template - `[wpsl]`, `{{getCtrlKey()}}` - instead of the content that instruction was meant to produce |
| `facts-locked-in-images` | text that is pixels rather than characters |
| `facts-locked-in-pdfs` | prices, and separately documents of record, that exist only as a download |
| `video-transcripts` | a statement made only aloud, with the silent-wallpaper case excluded |
| `iframed-main-content` | a separate document the parent does not contain |
| `image-alt-coverage` | images carrying no `alt` attribute at all |
| `core-facts-present` | price, location or service area, contact route, founding year — each found *with the sentence that states it*, or missing |
| `core-fact-founding-facts` | the refusal marker: a year beside a word that puts something into existence was found and could not be attributed, so no absence is asserted |
| `core-fact-location-or-service-area` | the same refusal for an address-shaped run this audit could not confirm |
| `answer-first-paragraphs` | whether the first sentence under each heading asserts or defers |
| `heading-hierarchy` | a page with no H1 at all, read from both the visible document and the markup |
| `headings-name-their-topic` | a heading whose vocabulary the page never uses again |
| `long-sentences` | prose too long to excerpt without changing its meaning |

## 5. Stale or uncorroborated facts — 9 checks

Can a consumer tell how current this is, and does anything outside the site agree with it?
All in `freshness-corroboration-audit`.

| Check | What it settles |
|---|---|
| `content-freshness` | an archive that stopped, distinguished from a deep one still publishing |
| `date-signals-present` | an article with no date a reader or a machine can use |
| `footer-copyright-year` | the cheapest liveness signal, and only where it is the site's own notice |
| `stale-year-references` | copy asserting it is current as of a year that has passed |
| `sitemap-lastmod-coverage` | how much of the sitemap carries a date at all |
| `sitemap-lastmod-recency` | what crawlers use to decide re-fetch frequency |
| `off-site-profile-breadth` | how many independent sources could corroborate a claim — the strongest signal in the field study |
| `profile-links-resolve` | whether the sources the brand names still exist |
| `fact-consistency-across-pages` | the site disagreeing with itself, which is worse than saying nothing |

## 6. Entity ambiguity — 4 checks

Can a machine tell this brand from the other things that share its name?

| Check | Skill | What it settles |
|---|---|---|
| `entity-ambiguity` | freshness-corroboration | how many Wikidata entities share the label, and whether the site disambiguates itself |
| `entity-definition` | fact-extractability | whether any sentence anywhere says what this is |
| `definition-on-the-homepage` | fact-extractability | whether it says so where a machine arriving cold will look |
| `brand-naming-consistency` | fact-extractability | whether the names the site *asserts* for itself agree |

Cross-referenced: `brand-name-in-markup` (signal 3) is the same question asked of markup
against markup, and `organization-markup`'s `sameAs` completeness is what anchors the entity
to sources outside the site.

## 7. Weak on-site orientation, no context retention — 19 checks

A visitor sent by an answer arrives mid-journey, deep in the site, carrying context the site
cannot see. All in `engagement-audit`, which is mechanism G, this marketplace's own extension
to the model.

| Check | What it settles |
|---|---|
| `homepage-orientation` | whether the front door says what this is and offers something to do |
| `primary-navigation` | at least three top-level destinations, and whether they are hidden rather than absent |
| `homepage-links-into-the-site` | how many pages of the site a machine can reach from the front page at all |
| `dead-end-pages` | no internal link, no call to action and no form — nowhere to go at all |
| `onward-links-specific-to-the-page` | a next step belonging to *this* page rather than the site-wide menu |
| `breadcrumb-navigation` | the visible trail, read from the addresses rather than a class name |
| `consistent-site-chrome` | pages missing the navigation the rest of the site carries, judged per language edition |
| `orphan-pages` | addresses the sitemap advertises that nothing links to |
| `broken-internal-links` | links that lead nowhere, with a refusal recorded as unchecked |
| `title-body-alignment` | whether the page delivers what the title promised and earned the click |
| `page-headings-name-their-own-page` | a heading that names the site rather than the page |
| `too-many-top-level-headings` | a page whose ten or more distinct H1s name no one subject |
| `landing-page-answers` | stock state, delivery promise and returns policy beside the price |
| `landing-page-leads-with-the-price` | whether the answer is in the first screen or under the brand story |
| `intrusive-interstitials` | content demanded before content given |
| `form-friction` | enquiry forms long enough to end the visit |
| `mobile-viewport` | most people arriving from an assistant are on a phone |
| `readability` | walls of text with no entry point to scan |
| `page-weight` | pathological weight and third-party script counts, reported only at extremes |

## What the mapping is not

It is a coverage claim, not an accuracy claim. Every signal has at least four checks behind
it and none is uncovered, but the accuracy of those checks is measured separately, in
`how-the-checks-were-built.md` (specificity, by breaking one property at a time) and
`cited-vs-uncited-study.md` (whether the thresholds are fitted to the sites they were set
on). A signal covered by many checks is not thereby well covered.

---

# The four Round 2 failure modes

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
| Few independent sources corroborate the brand | `off-site-profile-breadth` | freshness-corroboration-audit |
| No Wikidata or Wikipedia anchor | `entity-ambiguity`, `off-site-profile-breadth` | freshness-corroboration-audit |
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
