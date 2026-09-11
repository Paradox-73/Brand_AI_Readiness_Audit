# Entity disambiguation

When several different things share a name, a system either picks one or blends them. Without
explicit markers it usually picks the better-documented one — which is rarely the smaller
brand.

This is mechanism D's second half: agreement across sources builds trust, but agreement is
only useful if the sources are agreeing about *you*.

---

## What mistaken identity looks like

- A brand shares its name with a dictionary word (Apex, Summit, Beacon, Harbour, Vellum) and
  gets blended with the general concept.
- A brand shares its name with a larger company in another sector, and inherits that
  company's facts.
- A brand shares its name with a historical figure, a place, or a species.
- Two companies in the *same* sector share a name across different countries — the worst
  case, because the facts are plausible enough that a blend is not obviously wrong.

The failure is quiet. The assistant produces a fluent, confident answer that mixes two
entities, and nothing signals that it has done so.

---

## How the check works

Up to three requests to Wikidata. The first searches its public API for the brand name and
counts matching entities; a second search is spent on a shorter form the site itself publishes,
where the declared name has one (see "Searching a string that is not a name"). The last asks
all of the matches at once — up to ten ids in one call — which website each says is its own
(`P856`), so the item that *is* this brand can be told apart from the ones that merely share
its name. The search API does not return that property, and without that request the brand's
own entry was counted among the entities colliding with it: a Spanish manufacturer with an
article in eight Wikipedias was told to create an item if one did not exist.

| Matches | Site disambiguates itself | Result |
|---|---|---|
| 0–1, none declaring a different website | — | Not applicable: no collision to resolve |
| 1+, one declaring a different website | no | `medium`, at high confidence |
| 2–3 | no | `medium` |
| 4+ | no | `high` |
| any | yes | Not applicable, with the reason stating what disambiguates it |

### Whose website is whose

An item is this site's own only when the website it declares is this site's, and the two are
compared on the **registered domain**, not on the leftmost label of the address. One
organisation's addresses differ from each other in two ways and only two: a subdomain in front
(`docs.`, `uk.`, `www.`), or a different national registry on the same registered label (`.es`
beside `.com`). A label that matches because it is a *subdomain of somebody else's* registered
domain is neither of those.

Comparing the leftmost label produced the worst statement in one batch of real reports. An
auto-generated documentation site for an unofficial third-party client library, hosted at
`<the library's name>.<a documentation platform>.test`, matched the item for the large service
the library talks to, whose `P856` is `<the same name>.test`. The report told the project that
the service's encyclopedia item was its own, said there was no name collision to resolve, and
named that Q-id in the fix step as "your Wikidata item" — on the one site in the batch whose
name genuinely collides with something much bigger.

The inverse of that mistake is why a single confirmed collision is now reported. Two items
sharing a label could be a coincidence of spelling, which is what the two-entity threshold
guards against. An item that publishes its own address is not a coincidence: it states which
website it is about, and the audit knows which website it read. Where this site's own address
is itself a subdomain under a third party's registered domain, the addresses cannot decide
whether the two are one organisation, and the finding says so and drops to medium confidence.

**"Disambiguates itself"** means either:

- the site links to Wikidata or Wikipedia in `sameAs` or its footer — an explicit anchor to a
  specific entity, which is the strongest available marker; **or**
- it declares an `alternateName`, *and* a `description`, *and* links three or more off-site
  profiles that name the brand, on any recognised platform.

Wikidata is used because it is public, free, has no rate limit worth worrying about, returns
structured results with human-readable descriptions, and is itself a source many systems
consult. Three requests at most, inside a budget of sixteen for the whole skill — the rest of
which goes on one robots.txt per profile host and one HEAD per profile link that can be
verified.

### Reading the count honestly

The table above has a row for 0–1 matches, and the two ends of that row are different facts
that have to be written differently.

- **One other entity.** The count is below the threshold this check reports on. It is not
  zero, and the sentence may not say it is. A verdict that named another entity and then
  concluded "so there is no name collision to resolve" contradicted itself inside one
  sentence, on a product whose name is also an ordinary technical term.
- **Nothing at all.** Wikidata matches labels and aliases by prefix, so a search that
  returns literally no item — not even the brand's own — has almost always been given the
  wrong string rather than found an unheard-of name. The name comes from the site's own
  markup and can arrive with a leading article, half a tagline, or a legal suffix attached,
  and none of those appear in a Wikidata label. The same product returned five entities
  under its real name and zero under the form the audit was handed.

  So a zero is reported as a fact about the string, with the strings that were searched
  named in the reason, and the "does this brand have a Wikidata item" signal is left unset.
  Reported as "no collision" it is wrong twice: the collision is real, and the unset signal
  read as `false` sends the report on to recommend creating an item the brand already has.

### Searching a string that is not a name

The budget is two searches, and printing the second one as advice to the reader is not
spending it. A town's declared name was its legal class plus the place — that string matched
no label at all, and the audit's own reason told the reader to "search the name by hand,
without any leading article or legal suffix" while the second lookup sat unspent. The same
name without the class prefix returns the place's own item, with a one-line description,
immediately.

So the shorter form is derived and searched. A candidate has to be a shortening of the
declared name — its comparison key sits inside the declared name's key — and it has to be a
form the **site itself publishes**: the name minus a leading article, minus a leading
administrative class (`Town of`, `Ciudad de`, `Stadt`), an `alternateName`, an
`og:site_name`, or the leading words that spell the domain. Items are then kept only where
`names_are_one_name` says the label is that name, which is the marketplace's one strict
matcher — a substring test in its place once counted fifteen unrelated encyclopedia articles
as one brand's profiles, and a candidate loose enough to find *an* entity for everything will
claim the wrong one, which is worse than finding none.

Where no such form exists, the reason says why rather than assigning homework. The commonest
case is a declared name that is a whole page title, separators and all: no Wikidata label is
written that way, and guessing which segment is the organisation is exactly the aggression
above. That is a defect in the string the check was handed, and the reason names it as one.

### What it cannot tell you

It counts entities sharing a name. It **cannot** tell you which one an assistant currently
prefers, or whether a blend is already happening. It detects the *precondition* for mistaken
identity, not the event.

It also cannot tell you the brand has no Wikidata item. It can tell you that a name search
returned none and that no returned item claims this host as its official website — which is
a different, narrower statement, and it is unavailable at all when either request fails.

Where the agent runtime provides web search, the skill records a suggested query
(`signals.suggested_web_search`) rather than guessing. The query is recorded, not scraped
results — a query is reproducible and attributable; a scraped snippet is neither.

---

## The markers that actually separate entities

In descending order of how much work each one does:

### 1. A Wikidata item, linked from `sameAs`

The strongest marker available, and the one most small brands skip. It gives every consumer a
stable identifier for *this* entity rather than a name to guess with.

Creating one:

- Sign in at wikidata.org and create an item.
- **Label** = the brand name exactly as you write it everywhere else.
- **Description** = a short noun phrase that distinguishes it, e.g. "British architectural
  ironmongery merchant" — this is the line a disambiguation list shows.
- **Statements**: `instance of` (business/enterprise), `inception` (founding date),
  `headquarters location`, `industry`, `official website`.
- Add the URL to `Organization.sameAs` on your site so the link is stated from both ends.

Wikidata has notability requirements. If the brand does not meet them the item may be
deleted, and gaming that is a bad idea. The other markers below still apply.

### 2. Founding year, location and industry in `Organization` markup

`foundingDate`, `address`, and a specific `description`. These three fields are what separate
same-named entities more reliably than anything else, because they are rarely identical
between two companies sharing a name.

### 3. `alternateName`

Declares the other forms people write, as the *same* entity rather than as different ones.
Include the legal name if it differs from the trading name, common abbreviations, and the
form without punctuation.

### 4. A disambiguating phrase in prose

Never let the bare name stand alone in the opening sentence:

> ~~"Vellum has worked with brands since 2016."~~
>
> "Vellum Studio, the Leeds brand design consultancy, has worked with brands since 2016."

The apposition costs four words and travels with any sentence lifted out of the page. This
is the cheapest fix on the list and the one most often missed.

### 5. Corroborating profiles saying the same thing

Each with the identical name and the identical one-sentence description, each linking back to
the site. Three or more is where a brand stops being a single unlinked claim.

**Which places, though, depends on what the thing is.** A company page on a professional
network and an entry in a startup-funding database are corroboration for a company and noise
for anything else: a national weather service was told to claim both, and it has neither an
employer reputation nor an investment history. Ask `site_kind(snapshot)`. A public body has a
register entry, an encyclopedia article and a data portal; a program has a repository host, a
package index and a forum; somewhere with a front door has a mapping service and review sites;
a shop has the marketplaces it already sells on. Where the classifier cannot decide, name the
general set and let the reader pick — a wrong guess about what a site is caused the failure
above, so silence beats confidence here.

---

## Self-contradiction first

Before syndicating anything outward, make the site agree with itself. Syndication propagates
whatever it is given, and a brand that disagrees with its own pages will propagate the
disagreement to every directory it reaches.

The check reports `high` when:

- `Organization.telephone` does not match the number shown on the same page;
- `Organization.address.postalCode` does not match the code shown on the same page;
- more than one distinct `Organization.description` is published across the site.

The third is the most common and the least noticed: the boilerplate was written once, then
edited on one page and not the others, and now the site publishes three slightly different
descriptions of what the company is. Every one of them is a source that half-agrees with the
others.

**Scope note.** This skill owns telephone, address and description. `structured-data-audit`
owns name and price mismatches. The two do not overlap.

---

## The canonical boilerplate block

The fix that resolves most of this at once. One paragraph, written once, used verbatim
everywhere:

```
<Brand> is a <category> that <does what> for <whom>. Founded in <year> in <city>,
it <one distinguishing fact>.

<Street>, <City>, <Postcode>, <Country>. <Telephone>. <Email>.
```

Store it once — a CMS field, a template partial, a shared document the marketing team copies
from — and propagate. Never let two versions coexist.

Then push the identical text to: `Organization.description`, the about page, the press page,
every off-site profile the brand keeps — whichever those are for this kind of site, per the
section above — app store listings, every directory entry, and the boilerplate paragraph at
the foot of press releases.

Identical wording across independent sources is the strongest agreement signal available, and
it costs nothing but discipline. It is the only recommendation in this marketplace with no
condition attached.
