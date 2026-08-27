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

One request to Wikidata's public search API for the brand name, counting matching entities.

| Matches | Site disambiguates itself | Result |
|---|---|---|
| 0–1 | — | Not applicable: no collision to resolve |
| 2–3 | no | `medium` |
| 4+ | no | `high` |
| any | yes | Not applicable, with the reason stating what disambiguates it |

**"Disambiguates itself"** means either:

- the site links to Wikidata or Wikipedia in `sameAs` or its footer — an explicit anchor to a
  specific entity, which is the strongest available marker; **or**
- it declares an `alternateName`, *and* a `description`, *and* three or more authoritative
  profiles.

Wikidata is used because it is public, free, has no rate limit worth worrying about, returns
structured results with human-readable descriptions, and is itself a source many systems
consult. One request, capped at four for the whole skill.

### What it cannot tell you

It counts entities sharing a name. It **cannot** tell you which one an assistant currently
prefers, or whether a blend is already happening. It detects the *precondition* for mistaken
identity, not the event.

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

LinkedIn, Crunchbase, Google Business, industry directories — each with the identical name and
the identical one-sentence description, each linking back to the site. Three or more is where
a brand stops being a single unlinked claim.

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
LinkedIn, Crunchbase, Google Business, app store listings, every directory entry, and the
boilerplate paragraph at the foot of press releases.

Identical wording across independent sources is the strongest agreement signal available, and
it costs nothing but discipline. It is the only recommendation in this marketplace with no
condition attached.
