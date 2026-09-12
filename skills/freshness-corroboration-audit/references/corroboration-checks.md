# The freshness and corroboration checks, one section each

`SKILL.md` names each check and the severity it can reach. This file holds the measurement:
what each check reads, where it declines, and the real report behind every exclusion.
`references/entity-disambiguation.md` is the companion — what makes two same-named entities
separable, which markers matter most, and how to create a Wikidata item that holds.

---

## The register: ten check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report. Every finding here carries `mechanism="D"`. Sixteen extra network requests at most
across the whole skill: Wikidata's robots.txt, then up to three lookups to Wikidata only where
that file allows them, and the rest robots.txt and profile probes. Every third-party host is
asked only where its own robots.txt permits it - there is no exception. Five of the ten make
none at all.

| Check id | The question | What it reads | Threshold, and where it came from | Ceiling |
|---|---|---|---|---|
| `content-freshness` | Has the site stopped publishing, as distinct from having a deep archive? | every article page's newest date signal, from `dates.machine_readable` and `dates.visible` | `STALE_MONTHS = 18` — where "recent" stops being defensible. `STALE_SHARE = 0.7` — most of the library being old is a programme problem, not one old post. `STILL_PUBLISHING_MONTHS = 12` — **both conditions are required**, because a blog running ten years always has most of its posts past 18 months, and that is arithmetic rather than a defect | **medium**, `stale-content` |
| `date-signals-present` | Can a consumer tell how current a page is? | `dates.has_any` per page, plus the sitemap's `<lastmod>` for the same address | a section counts as a dated section at **two** dated pages and **half** the section — a majority is what makes the inference sound. Listing pages are removed from **both** sides of the arithmetic | **medium**, `no-date-signal` |
| `footer-copyright-year` | Is the newest year the site states about itself behind? | copyright notices re-read from the page text, with the party each one credits | `COPYRIGHT_LAG_YEARS = 1` — a footer two calendar years behind reads as abandoned. `_NOTICE_TAIL_CHARS = 80` — long enough to reach the name a credit gives, short enough not to swallow the next paragraph | **low**, `stale-content` |
| `stale-year-references` | Does the copy claim to be current as of a year that has passed? | sentences whose subject is the site, matched for a currency claim | `AS_OF_LAG_YEARS = 2` — "as of 2023" written in 2026 actively misinforms. `AS_OF_HISTORICAL_YEARS = 15` — the floor: 1900 on a biodiversity page and 1917 on a founding story were both reported as copy asserting a currency it does not have | **low**, `stale-content` |
| `sitemap-lastmod-coverage` | Do sitemap entries carry a date a crawler can parse? | every `<lastmod>` in every sitemap record | under 50% coverage. The 50% is **chosen, not measured** — there is no justification for the number in the code, only for the check | **low**, `no-date-signal` |
| `sitemap-lastmod-recency` | Has anything been touched in the last year? | the newest and the median parsable `<lastmod>` | five parsable entries minimum, and twelve months. The median is taken **median-low** rather than averaged, because averaging two dates is not defined | **low**, `stale-content` |
| `off-site-profile-breadth` | How many independent sources could corroborate a claim? | `social_profiles` and `declared_profiles` per page, plus the off-site addresses named in an `/llms.txt` this run actually read | `PROFILE_BREADTH_GOOD = 6` and `PROFILE_BREADTH_THIN = 3`, from `PROFILE_BREADTH_NAMED_MEAN = 7.2` against `PROFILE_BREADTH_UNNAMED_MEAN = 4.6` — **measured**, in a within-category study of 29 crawlable sites across six categories, and the only measure that moved the same way in all six. See `skills/audit-orchestrator/references/cited-vs-uncited-study.md`. `CITATION_MIN_PAGES = 5` — below five crawled pages there is no such thing as "linked from only one page" | **high**, and only at **zero** profiles; **medium** at one or two, **low** at three to five |
| `profile-links-resolve` | Do the sources the brand names still exist? | one HEAD per profile on the six platforms that answer honestly, every dead verdict re-asked with a GET | no numeric threshold; only 404 and 410 count as gone | **medium** for more than one, **low** for one, `dead-profile-link` |
| `entity-ambiguity` | How many things share this name, and does the site tell itself apart? | Wikidata's robots.txt first - where it disallows `/w/`, no lookup is made and the check is declined as unchecked - then Wikidata's search API for the brand name and one derived shorter form, then one batched lookup of `P856` on the matches | `WIKIDATA_AMBIGUITY_THRESHOLD = 2` — a shared label could be a coincidence of spelling, which is why two are wanted; a `P856` naming a different host is not a coincidence and is reported at a count of **one**. Four or more matches raises it to high; that 4 is chosen, not measured | **high**, `entity-ambiguity` |
| `fact-consistency-across-pages` | Does the site disagree with itself? | `Organization` telephone, postal code and description, against the same page's visible text and against every other page declaring them | telephone matched on its **last seven digits**, the same rule the core-facts check uses. Branch nodes are dropped on a multi-location site, so legitimate per-branch variation is not reported as self-contradiction | **high** at medium confidence, `nap-inconsistency` — the only fixed-high finding here |

### What the date reader can and cannot read, stated plainly

`parse_date` in this skill accepts four shapes and no more: an ISO `YYYY-MM-DD`, an English
month name with a day and year in either order, and a numeric slash date whose year is
restricted to `19xx` or `20xx`. Anything outside 1990 to next year is discarded.

A date in another calendar is read by a second path, not by `parse_date`, because
`28 สิงหาคม 2569` is not a string any date library resolves. The extractor converts
it and records the result as `gregorian_year`; `newest_date` reads that field. Four
calendars, each requiring a mark the page printed itself:

| Calendar | Where it is met | What the page has to print | Converted by |
| --- | --- | --- | --- |
| Buddhist era | Thailand | Thai script or a declared `th`, or `พ.ศ.` before the year | `buddhist_era_dates`, `thai_era_dates` |
| Japanese era | Japan | an era name — `令和`, `平成`, `昭和` — in front of the year | `japanese_era_dates` |
| Hijri, lunar | the Arabic-speaking web | `هـ`, `هجري` or `قمري` after the year | `hijri_dates` |
| Hijri, solar | Iran, Afghanistan | `هـ.ش` or `شمسی` after the year | `hijri_dates` |

Three digit sets are read for all four: ASCII, Arabic-Indic `٠-٩`, and the Extended set
`۰-۹` that Persian, Dari, Pashto and Urdu pages print.

**The conversion establishes a year and nothing finer**, so the date taken from it is the
first of January — the earliest the page can be, and therefore the reading that cannot
overstate freshness. A Thai page dated 2569 is reported up to eight months older than it is
and never newer. Guessing a month would have this audit call a page current on a date nobody
published, and a wrongly converted date is worse than an unread one.

**A number carrying no mark is read as no calendar.** `31/03/2563` on a page with no Thai
anywhere and no declared language yields nothing, and `1447` on its own is a street number, a
product code and a price more often than it is a year. Where the marks are absent,
`date-signals-present` reports the page as dateless, which is what the page gives a machine
to work with.

---

# Freshness

## 1. Content staleness

Only where three or more article pages were crawled. Compute each article's newest date signal.

Report **medium** when 70% or more are older than 18 months *and* the newest is over 12 months
old. Both conditions matter: an active blog with a deep archive is healthy, and only the second
condition distinguishes it from an abandoned one. 18 months is where "recent" stops being
defensible.

Where the share is met but the newest article is 12 months old or less, say so in the
not-applicable reason with the share in it — "a deep archive on a site that is still publishing"
— rather than a bare pass, or the report files a 93%-stale library under "nothing to report".

---

## 2. Missing date signals

A different problem from an old date. An article with no `<time>`, no
`datePublished`/`dateModified` and no visible date in its text gives a consumer no way to judge
currency at all. **Medium**. Do not expect dates on evergreen pages like pricing or contact.

**And do not expect one on a page whose job is to point at other pages.** An index carries the
dates of the items it lists, or none, and neither of those is a date of its own — so a listing is
left out of both sides of the arithmetic: it cannot be counted as undated, and the dates it
borrows cannot be what makes its section count as a dated one. A blog hub linking three category
listings was counted among "13 pages where one is expected" and told to add "Published `<date>`,
last updated `<date>`" under its headline.

The same one detector decides this for every check in this skill, so a page cannot be an index to
one and an item to another.

---

## 3. Footer copyright year

More than one year behind the reference date is **low**. It is the cheapest liveness signal on a
site, and a stale one colours how everything else is weighed.

**Only the site's own notice, and only where it sits.** A library reproducing donated text carries
the publisher's line mid-document — "© 2000, text supplied by `<publisher>`" — and its own pages
carry no notice at all; that year was reported as the site's footer being 26 years stale. A
copyright line that names a party other than the audited brand is that party's, and its year says
nothing about this site. A line that names nobody, which is the ordinary footer, is the site's and
still fires.

Say "in the footer" only for a notice that is at the end of the page text; anywhere else it is "on
the page", because the fix names a footer template a mid-document credit has nothing to do with.

---

## 4. Stale year references in copy

"As of 2023", "last updated 2022", "prices for 2021" — two or more years behind is **low**.

Match only phrases that assert the copy is *current* as of a year. A bare "in 2019" is usually a
founding date, and matching it would turn every about page into a false positive.

---

## 5. Sitemap `lastmod` distribution

Report the newest and the median. Nothing modified in over a year is **low**: `lastmod` is what
crawlers use to decide re-fetch frequency, so a frozen site is re-read rarely and any change you
*do* make takes longer to be noticed.

---

# Corroboration

## 6. Off-site profile breadth

Collect every off-site profile linked from the pages or listed in `sameAs` that names this brand, and
count them **across every recognised platform**, not a hand-picked shortlist.

A `<link rel="me">` counts whatever host it points at: that is what the attribute means, and it is the
only way a Mastodon account can be found at all, since Mastodon has no fixed domain and so cannot sit on
any list. A database whose entire off-site presence besides its code host was a Patreon and two Mastodon
accounts was told it links one profile, because a list is what the check had.

Any other address counts only on a host where brands keep accounts
(`audit_common.on_a_profile_platform`). A link to a magazine article about the brand is coverage, not an
account, and nothing in its shape tells the two apart. The one rule is `audit_common.could_be_an_account`,
and `structured-data-audit` builds its paste-ready `sameAs` block with it too, so the profile count and the
`sameAs` block never list different accounts.

When you say what you looked at, name the vocabulary and not only the place: "the platforms this audit
recognises" is the real limit, and "we looked in the footer" hides it.

Six or more is a pass: that is the level typical of brands assistants name in our study. Three to five is
**low**, fewer than three is **medium**, and linking to none at all is **high** — zero is not "a bit
thin", it is a brand with no independent source that could corroborate a single claim it makes.

### A profile is an account, not a platform

Three readings separate the two, and none of them names a company.

An account is a handle *inside* a platform, so an address with nothing after the host is that platform's
own front door — a "powered by" credit, a payment badge — and not a profile on it.

A host the site loads its code from is what the site is built on: the crawl records those per page as
`scripts.third_party_hosts`, and a platform serving a shop's theme from `cdn.<platform>` while describing
itself at `www.<platform>` is one thing under two names, so read the domain a role-named host sits under
as well as the host.

A leading label that names a machine's job — `cdn.`, `assets.`, `analytics.`, `widget.` — or a path that
is a file rather than a page, is infrastructure either way.

One hosted store was counted at **8 profiles where the truth was 3**, and passed this check, because five
addresses of exactly those kinds were let in.

### `/llms.txt` is a file the site writes about itself

So read it and count the off-site addresses it names. It is not the assertion `sameAs` is, though:
`sameAs` can only say "these accounts are me", while a paragraph of prose names whatever its author found
useful, and on a hosted platform the author is the platform.

So an address here earns its place the way a link on a page does — it has to be an account, on a host the
site does not merely load its code from — and a document the file tells an agent to go and fetch (a
specification, a schema, another site's instruction file) is something to read, not somewhere anybody
keeps an account.

What survives still counts wherever it is written: a project whose own `/llms.txt` links its public
repository drew "The site links to no off-site profile at all" as the single high-severity finding of its
report, from a run that had already fetched that file. Where a snapshot carries the record without the
body, say so in the evidence rather than passing over it; where the file named addresses and none was an
account, say that too, because an address dropped in silence reads as one never looked for.

### The title of an absence may not claim more than the evidence under it

Both titles here name what was looked at — the crawled pages and `sameAs` — because that is what a reader
quotes back, and the evidence line was already scoped while the title was not.

### A name test that cannot pass must not be the thing that rejects

An account linked from the body of one page, absent from the site's chrome and from `sameAs`, is read as a
citation of somebody else's account unless its handle carries a name the site publishes for itself. That
comparison keeps letters and digits and drops everything else, so a brand name written in Japanese, Korean,
Arabic, Greek or Cyrillic reduces to nothing and *no handle on earth* can match it. A city government's
five official accounts — one per crawled page, none in the footer — were all dropped on that basis, and the
report led *Start here* with "Distinct off-site profiles linked: none".

Two readings fix the class, not the country. The names a handle is compared against include **every label
of the site's own hostname**, which are the romanised forms the site publishes about itself — not just the
leftmost one, because on a civic, university or agency host the leftmost label is the department (`city.`,
`physics.`) and the name is the label after it. And where no available name yields a single comparable
letter, the handle test **abstains** rather than rejecting — the alternative is dropping an account for
failing a test that could never have been passed.

### `checked[]` states the reading the code performs

Not the one it was meant to feel like. This finding used to say it read "links anywhere on the crawled
pages, body, header and footer alike"; a body link on a single page is exactly what it discards. A method
claim the code does not implement is worse than a wrong count — a reader cannot argue with an untold rule.

### Why breadth rather than a shortlist

An earlier version counted only nine hand-picked "authoritative" platforms and fired on 94% of named brands
and 100% of unnamed ones, separating nothing. Breadth across every platform is what tracks the divide:
brands assistants name linked a mean of 7.2 distinct profiles, comparable brands they do not name 4.6 — the
only measure pointing the same way in all six categories tested. A fact stated only on the brand's own site
is one source's word for it.

### Name platforms this site could actually be on

The finding fires whatever the site is — a program gains from a repository host and a package index exactly
as a shop gains from a photo platform — but the platforms the fix names must not.

A national weather service was told to open a company page on a professional network and a profile on a
startup-funding database: one describes employers, the other investments, and it is neither. Ask
`site_kind(snapshot)` and name the places that kind of thing lives — registers and an encyclopedia for a
public body, a repository host and a package index for a program, a mapping service and review sites for
somewhere with a front door, marketplaces for a shop.

Gate with `might_be`, never with `is_certainly`: it answers yes on every site the classifier could not
place, so an unplaced site keeps exactly the advice it got before.

---

## 7. Do those profile links resolve?

Breadth in check 6 counts what the site *declares*, which is what the study measured; this is the separate
question of whether those links still lead anywhere.

Send one HEAD request per profile, and where HEAD says 404 or 410 ask again with a GET before believing it —
a profile that 404s to an unauthenticated HEAD while serving 200 to a reader was reported as leading nowhere,
advising the owner to delete a working `sameAs` entry.

Report the ones still gone after that: one is **low**, more than one is **medium**. A 200 that ends on a
sign-in or challenge URL is **unchecked**: a sign-in wall is not an answer. A `sameAs` entry pointing at a
deleted page is worse than no entry — a claim the brand makes that does not check out, on exactly the signal
a machine uses to decide the brand is corroborated.

**Only ask platforms that answer honestly.** We requested a profile that certainly does not exist from each
platform and recorded the reply:

| Platform | Dead profile | Live profile | Verified? |
|---|---|---|---|
| LinkedIn, X, YouTube, GitHub, Wikipedia, Wikidata | 404 | 200 | yes |
| Instagram, TikTok, Medium, Pinterest, Threads | 200 | 200 | no - a 200 proves nothing |
| Crunchbase, Yelp, Glassdoor, Trustpilot | 403 | 403 | no - refuses every crawler |

So six platforms are checked and the rest are left alone. A 403, a timeout or a redirect to a sign-in page is
**unchecked**, never dead: reporting a live Instagram account as missing, because Instagram answers 200 to
everything, would be worse than not looking.

Do **not** subtract dead links from the breadth count in check 6 — that number is compared against a study
that measured declared links, and changing the measure would invalidate it.

---

## 8. Entity ambiguity

Query Wikidata's public search API for the brand name and count matching entities, then ask all the matches at
once which website each names as its own (`P856`), so the item that *is* this brand is not counted among the
ones colliding with it.

Two or more others, *and* no disambiguation on the site, is **medium** — **high** at four or more. Treat the
site as already disambiguated if it links to Wikidata or Wikipedia, or if it declares an `alternateName` plus
a description plus three or more linked off-site profiles. Where the agent runtime provides web search, record
the suggested query; record the query, not results scraped without attribution.

**An item is this site's own only if the website it declares is this site's**, compared on the registered
domain rather than the leftmost label. One organisation's addresses differ by a subdomain (`docs.`, `uk.`,
`www.`) or by a national registry (`.es` beside `.com`); a subdomain of *somebody else's* registered domain is
neither, and reading it as one told an auto-generated documentation site for an unofficial third-party client
library that the large service it talks to was its own entity, naming that Q-id as "your Wikidata item".

**A `P856` on a different host is a collision confirmed, and is reported at a count of one.** The two-entity
threshold exists because a shared label could be coincidence; an item that publishes its own address is not. A
site whose name belongs to a much larger organisation is exactly the site that needs telling, because an
assistant asked about it will answer about the other one. High confidence, or medium where this site's own
address is itself a subdomain under a third party's domain and could belong to either.

**The sentence has to agree with the count it just printed.** Below the reporting threshold is not the same as
nothing found. A verdict that named one other entity and then concluded "so there is no name collision to
resolve" contradicted itself inside one sentence; where the count is one, say it is one and say it is below the
threshold.

**Zero results is a fact about the string, not about the world.** Wikidata matches labels by prefix, so a name
returning nothing at all — not one item, not even the site's own — has almost always been mis-taken from the
page: it can carry a leading article, half a tagline, a legal suffix or an administrative class prefix that no
label uses. Report it as unchecked, name the strings searched, leave the "has a Wikidata item" signal unset, and
never call it "no collision".

**Spend the second lookup rather than printing it as advice.** The budget is two searches; where the first
returns nothing, derive a shorter form *the site itself publishes* — minus a leading article, minus a leading
administrative class (`Town of`, `Ciudad de`, `Stadt`), an `alternateName`, or the leading words that spell the
domain — and search that, matched with the shared strict name matcher. A candidate loose enough to find *an*
entity for everything claims the wrong one, which is worse than finding none. Where no such form exists, say why
— including that the string handed in is a page title with separators rather than a name — instead of telling the
reader to run the search this check could run.

---

## 9. Self-contradiction

A brand that disagrees with itself is the hardest case for anything weighing sources, and does more damage than
silence because it undermines every other fact on the site.

Report **high** when:

- an `Organization` telephone does not match the number shown on the same page;
- an `Organization` postal code does not match the one shown on the same page;
- more than one distinct `Organization` description is published across the site — the boilerplate is not one
  sentence but several.

**Scope note:** JSON-LD *name* and *price* mismatches belong to `structured-data-audit`. This skill owns
telephone, postal address and the description boilerplate. The two do not overlap.

---

## Why the not-applicable reasons are worded as they are

- **Fewer than three article pages were crawled.** There is no publishing cadence to assess.
- **No article carries a parsable date.** Reported as a missing date signal instead, not as staleness. The two
  have different fixes.
- **No article or press pages exist.** Dates are not expected on evergreen pages.
- **No copyright year appears anywhere**, or every copyright line found credits another party for material the
  site reproduces — the reason quotes the lines.
- **Fewer than five sitemap entries carry a parsable `lastmod`.**
- **Six or more distinct off-site profiles are already linked.** The reason names every one of them, and says the
  footprint is at or above the level typical of brands assistants name.
- **No profile is linked on a platform that answers honestly** about whether a profile exists, so there is
  nothing we can check without inventing the answer.
- **Every verifiable profile link resolved.** The reason names each one that was checked.
- **Wikidata returns fewer than two matches** and none declares an official website on a different host, or the
  site already disambiguates itself. Where the count is one, the reason names it.
- **Wikidata returns nothing at all** for any form of the name that was searched. Reported as a fact about the
  name string this audit was given, never as an absence of collisions. The reason names the strings searched, and
  either the shorter form derived and searched second or why none could be.
- **`--no-network` was passed, or Wikidata could not be reached.** An audit-environment limitation, recorded as
  such and never as a site defect.
- **A profile host answered 403, timed out, or redirected to a sign-in page.** Recorded as unchecked. It is never
  reported as a dead link, because a refusal is not an answer.
