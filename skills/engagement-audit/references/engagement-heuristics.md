# Engagement heuristics

Every threshold in `engagement-audit`, why it sits where it does, and what a visitor arriving
from an AI answer needs that an ordinary visitor does not.

---

## The visitor this skill is about

Someone sent by an assistant is not a normal visitor:

- They arrive **mid-journey**. The explaining has already been done for them, elsewhere.
- They arrive **with specific intent**. They are checking whether this is the right place,
  not browsing to find out what it is.
- They arrive **deep**, often not on the homepage, with no navigation history behind them.
- They arrive **carrying context the site cannot see**. The site does not know which question
  was asked, so it greets them exactly as it greets everyone.

That last point is the root cause of the bounce, and it is mechanism G. The fixes are
orientation, a next step, and a way back.

---

## Orientation

| Check | Threshold | Why |
|---|---|---|
| H1 present and specific | present, and not in the generic set | "Welcome", "Home", "Hello", "Untitled" and the like identify nothing. The set is a fixed list of those headings; there is no word-count rule, so a short brand name used as an H1 passes. |
| Primary CTA position | within the first **1,500 characters** of body text | Roughly the first screen. A CTA 4,000 characters down is not orientation, it is a footer. |
| Navigation size | **3 or more** top-level items | Under 3 gives nowhere to go. There is deliberately no upper bound: a 29-item megamenu is normal on a real retail site and costs a machine nothing to read. |
| Navigation labels | not `More`, `Menu`, `Other`, `Misc`, `Resources`, `Info`, `Information`, `Stuff`, `Items`, `Pages` — and only where they are *most* of the menu | These could head any menu on any site, so they name no destination. `Products`, `Services`, `Solutions` and `Company` were on this list and came off it: they are section names, and `Products / Solutions / Customers / Resources / Pricing / Company / Contact` is ordinary correct structure that used to produce a finding listing seven destinations and then saying four of them named none. |

### CTAs are recognised by shape, not by phrase list

The first version matched a fixed list of marketing phrases and missed most real CTAs —
"Browse the ironmongery range" and "Speak to the trade counter" are plainly calls to action
and matched nothing.

A link is now a CTA if **its first word is an imperative verb** (get, start, book, buy, shop,
browse, view, see, request, contact, call, order, try, download, subscribe, join, apply,
explore, discover, find, learn, read, watch, schedule, reserve, sign, register, talk, speak,
ask, enquire, compare, choose, select, plan, build, create, send, email, visit, check, claim,
take, open, configure, estimate, quote, hire, arrange…) **or** it carries button markup
(`btn`, `button`, `cta`, `call-to-action`, `primary-action` in its class, id or role).

This was a false positive found by running the clean fixture, and it is a good example of why
`good-site` exists.

---

## The page a deep link lands on

Everything above judges a visitor who arrived at the homepage and browsed. That is not the
visit this skill exists for. A shopper asks an assistant about one specific thing, is given
its name, clicks through, and lands on the page for that one thing already holding the
question — and the site shows them the page it shows everybody.

| Check | Threshold | Why |
|---|---|---|
| Purchase facts on a priced page | none of **stock state**, **delivery promise** and **returns policy**, on **half or more** of the site's priced pages | Price is the fact the assistant already gave them. What is left to decide the buy is whether the thing exists, when it arrives and what happens if it is wrong. A page answering none of the three has told a visitor what to want and nothing about how to get it. |
| Price position | first currency figure beyond the first **1,500 characters** of body copy, on **half or more** of them | The same window the call-to-action check uses, for the same reason: roughly the first screen. |
| A next step of the page's own | no internal link outside the set four fifths of the crawled pages also carry, on **half or more** of the deep pages | The menu is the same menu every page carries. Following it means starting again at the top of a search the visitor had already finished. |

### The class of page is established before anything is asked of it

A page joins the for-sale class only where it says so itself: the crawl typed it a product
page, or it publishes product markup about itself — and either way it carries a price, and it
is not a listing.

- A **grid of twelve items with twelve prices** answers a browsing visitor. The questions here
  are about the one thing somebody arrived asking for, and a category page has no one thing.
- An **essay quoting a competitor's licence fee** is not a page anybody buys from. In an
  earlier version of this marketplace a stray currency figure in an article was enough to open
  every pricing check on a consultancy and a medical charity.

### Three readings of each fact, and two of them need no prose

| Fact | Markup | Links | Words |
|---|---|---|---|
| Stock state | `availability`, `inventoryLevel`, `availabilityStarts` | — | "in stock", "sold out", "add to basket" and the like |
| Delivery | `shippingDetails`, `deliveryTime`, `estimatedDeliveryDate` | a link whose path names shipping, delivery, postage or dispatch | "free delivery", "ships within", "estimated delivery" |
| Returns | `hasMerchantReturnPolicy`, `returnPolicyCategory`, `merchantReturnLink` | a link whose path names returns, refunds, exchanges or cancellation | "returns policy", "30-day refund", "money-back" |

Markup is read from JSON-LD, Microdata and RDFa alike — a shop whose theme states its stock in
`itemprop` attributes has stated it, and reading JSON-LD alone would report a page that
answers every question as answering none.

Paths are ASCII on sites whose prose is in any script at all, so the middle column keeps
working where the right-hand one cannot. `find_prices` matches a currency symbol or an ISO
code and `availability` is a schema.org property, so neither the price measurement nor the
first-screen measurement is an English test at all.

Where the check genuinely cannot read, it says so rather than guessing. On a site whose prose
these patterns are not written for, a page carrying no commerce markup leaves one reading
standing out of three; those pages are **set aside and counted in the evidence**, because
"this page does not say when it arrives" would otherwise be a statement about this audit's
vocabulary rather than about the site.

### Document order is the reading order on a phone

Which is what makes the price position measurable at all, and also its limit. A price a wide
screen floats beside the opening paragraph is read here as sitting wherever its markup sits,
so the finding names the document order it measured. A price stated only in the markup has no
position in the copy and is not measured: reporting one as buried would be a claim about a
figure the visitor never reads there in the first place.

### Position is not asked of a next step

A section trail written into a page's header is as much a way onward as a block under the
copy. Judging by position reported four location pages whose header carries a trail into the
section they sit in as offering a visitor nothing but the menu. So the measurement is what a
link points at, not where it sits: every internal link on the page — body, menu and footer
alike — against the links four fifths of the crawled pages carry, which is the same
`CHROME_CORE_SHARE` the site-chrome check uses to separate site furniture from a section
menu.

### The floors, and what happens without them

| Floor | Value | What it prevents |
|---|---|---|
| Comparable pages before any of the three speaks | **3** | A finding here says the template is wrong. "1 of 1 product pages" is a sentence about whichever page the crawl happened to reach. |
| Share of them that must fail | **half** | One page built from a different template is not a template, and telling an owner to edit something that is right on nine pages in ten is how a check earns a reputation for guessing. |
| Crawled pages before "four fifths of them" means anything | **8** | On a five-page crawl four fifths is four pages, so one section menu becomes site furniture and every page reads as carrying nothing of its own — a finding about the crawl, not the site. |

---

## Dead ends and wayfinding

| Check | Threshold | Why |
|---|---|---|
| Dead-end page | **no** internal link in main content **and** no CTA **and** no form | All three conditions required. A cul-de-sac is a page with no way onward at all, not a page with few: a landing page with a strong CTA and an enquiry page whose whole purpose is its form both pass. |
| Orphan pages | in the sitemap, crawled, linked from no crawled page | Only pages actually visited are judged. A sitemap URL never fetched might well be linked from a page outside the crawl budget, and guessing would be a false positive. |
| Broken internal links | 404 or 410; **over 20%** is high | Reuses statuses the crawl already has, then probes up to **40** more with HEAD, re-asking with a GET any target HEAD called dead. Above a fifth it is a maintenance failure, not an accident. |
| Breadcrumbs on deep pages | **half or more** missing | Deep types only: product, article, location, service, comparison. A breadcrumb on a homepage is meaningless. |
| Site chrome | pages sharing few or none of the site's usual nav links | Compared against the most common **non-empty** navigation signature — see below. |

### Why "non-empty" matters

Taking the plain majority signature meant the check bailed out on exactly the worst case: a
site where *most* pages have no navigation at all, making "no chrome" the mode and the
comparison meaningless. Found by running `dead-end-site`, where four of five pages have no
header.

A visitor who lands on a page with no navigation has no way back into the site. That page
becomes the whole experience, and when it ends, so does the visit.

---

## Title–body drift

Under **a third** of a title's content words appearing anywhere in its body text or its own
headings.

The title is the promise that earned the click — it is what the assistant showed and what the
visitor decided on. A visitor who does not see it confirmed in the first few lines assumes
they are in the wrong place, and the fastest way to check is to go back.

The brand suffix is stripped before comparing ("… | Brightpath Analytics" appears in every
title and would mask real drift), stopwords are removed, and matching is on the first six
characters of each token so plurals and simple inflections still count.

### Two kinds of page this cannot be asked of

Both are **set aside and counted** in the reason or the evidence, never reported. A page that
was not compared is not a page that passed.

| Set aside | Why the comparison is the wrong instrument there |
|---|---|
| **A listing page** — `is_listing_page`, the same test the skimmability rule uses | Its words are the names of the items it lists. A title naming a category, on the page listing that category, is delivered by the list. A new-arrivals grid of two kinds of goods was reported as missing both of those words, absent from the prose only because the prose is item names. |
| **A body whose script spells a whole syllable per character** — Hangul, kana, Han | The six-character prefix is what lets an inflected form count. Where one character is a syllable, six characters is longer than most whole words, so the window trims nothing and the test silently becomes an exact match on the surface form. A language that marks grammar by adding a syllable to the end of a word writes the noun with one particle in the title and another in the sentence, so the exact match fails on words the page does deliver: one homepage tagline was called undelivered with three of its four stems in the first paragraph. No shorter window recovers it — two syllables of a three-syllable stem matches unrelated words. Whitespace-token overlap is not measurable there at any threshold. |

Two details of the second one are easy to get wrong.

- **Read the script from the page body, not from the title.** `dominant_script` returns `None`
  below 20 letters and a title is shorter than that, so a gate asked of the title answers
  "ordinary spaced text" for every short non-Latin title there is.
- **This is not the spacing problem.** `UNSPACED_SCRIPTS` names the scripts that put no space
  between one word and the next, and Hangul is not one of them — Korean does use spaces. The
  defect is the size of the unit a character stands for, which is a separate property.

---

## Friction: only extremes are reported

Ordinary page weight is measured and deliberately **not** reported. Reporting every heavy
page would bury the findings that matter under numbers nobody will act on.

| Check | Threshold | Why |
|---|---|---|
| Page weight | over **2 MB** of HTML, inline script and inline CSS included | Not "heavy" — extreme. The heaviest of 302 real pages measured 1.26 MB, so 2 MB is still 1.45x the worst page seen. It was 3 MB, which is 2.4x anything observed and could never fire. |
| Third-party scripts | over **15** distinct third-party hosts | Beyond this, first paint is not under the site's control. It was 25, and across those same 302 pages the median page loads scripts from one third-party host, the 99th percentile from three and the worst from ten — so 25 was unreachable and 15 still means "unusual" rather than "a normal amount of tag manager". |
| Interstitials | an open modal/overlay in the delivered markup, or autoplaying video | Content covered before it can be read. |
| Enquiry forms | over **8** required fields | A visitor who arrived from an answer has invested one click, not a decision. |

### A cookie banner alone is not a defect

Most sites are legally required to show one. It is only reported if it covers the H1 and
first paragraph. Flagging every cookie banner would be the single largest source of noise in
this skill, and would be advice the site owner cannot act on.

Search and newsletter forms are excluded from the field-count check — a search box is
supposed to be one field, and a newsletter form is supposed to be short.

---

## Skimmability

| Check | Threshold | Why |
|---|---|---|
| Walls of text | **2 or more** paragraphs over 200 words | One long paragraph is a stylistic choice. Two or more is a pattern. |
| Subheading density | 700+ word page with a subheading only every 300 words | Under 700 words a page does not need internal subheadings. |
| Mobile viewport | missing | `medium` if every page, `low` if some. Most people arriving from an assistant are on a phone, and a page they must pinch to read is a page they leave. |

Visitors scan before they read. A page with no visual entry points offers nothing to scan, so
a visitor cannot tell in three seconds whether their answer is here.

---

## Boundaries with other skills

Two pairs look like overlaps and are not:

| This skill | The other skill | Why both |
|---|---|---|
| **Visible** breadcrumb trail | `structured-data-audit` checks `BreadcrumbList` markup | Different fixes, different owners. A site can have one without the other, and both are worth having. |
| **Broken internal links** (`broken-links`) | `crawl-access-audit` reports the non-200 rate (`non-200`) | One is "pages on this site fail"; the other is "pages link to things that fail". Different root causes, so deduplication correctly keeps both. |
| **This page** states no stock, delivery or returns (`no-orientation`) | `fact-extractability-audit` asks whether **the site** ever states a price (`missing-core-fact`) | One is a template on which some pages answer a buyer and some do not; the other is a business that publishes no figures at all. A site can fail either without failing the other, and the fixes are a template edit and a pricing page. |
| **A next step of this page's own** (`dead-end`) | the dead-end check in this same skill | The dead-end check asks whether a page offers any way onward, and almost every real page does. This asks whether any of it is about the thing the visitor came for. Same root cause, one level in, so they are two findings from one skill rather than two skills reporting one defect. |

Newsletter capture is detected but **never reported as a finding**. It feeds the
`R-EMAIL-TEXT-FIRST` proactive recommendation (mechanism F), because having a newsletter is
not a defect and the guidance only applies if one exists.
