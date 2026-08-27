# Answerability rules

What makes a sentence liftable, with worked rewrites. The test throughout: **could this
sentence be pulled out of its paragraph and stand alone as an answer?**

---

## The entity definition

The single most valuable sentence on any site, and the one most sites never write.

An assistant needs to know *what a brand is* before it can say anything else about it. If the
site does not state it, the description gets assembled from whatever a third party wrote —
which is how brands end up described in a competitor's words, or as "a company that provides
solutions".

### The shape

```
<Brand> is a <category> that <does what> for <whom>.
```

Four slots, all load-bearing. The brand name is the grammatical **subject** — not "we", not
"our team", not a pronoun the reader is expected to resolve.

### What the check requires

- The brand name appears as the subject of a copular verb (`is`, `are`, `was`, `remains`).
- At least **20 characters of predicate** after it. *"Acme is a company."* is technically a
  definition and tells nobody anything.
- Inside the **first 150 words** or the opening headings of the homepage or about page.
  A definition below the fold is rarely the passage an assistant reaches for.

### Rewrites

| Before | After |
|---|---|
| "We help ambitious brands unlock their potential." | "Vellum Studio is a brand design consultancy that builds identity systems for companies with 50 to 500 employees." |
| "Welcome to Coppergate." | "Coppergate Ceramics is a pottery in York making wheel-thrown stoneware tableware for home cooks and independent restaurants." |
| "Innovation, delivered." | "Ironbark Supply is an architectural ironmongery merchant supplying hand-forged hinges and latches to restoration builders across the UK." |

Two failure modes, reported differently because the fixes differ:

- **Named but never defined.** The brand appears; no sentence says what it is.
- **Never named at all.** The opening uses "we" and "our" three or more times and the brand
  name does not appear. A machine reading that page cannot tell whose site it is. This is
  common and almost always unintentional — the writer knows whose site it is.

### Where else the same sentence goes

Verbatim, character for character: the about page, `Organization.description`, the LinkedIn
company page, the press kit, app store listings, every directory. Identical wording across
independent sources is the strongest corroboration signal available (mechanism D). Rewriting
it per channel throws that away for no gain.

---

## Answer-first sections

On pages a visitor arrives at with a specific question — pricing, FAQ, product, service,
location, comparison — the first sentence under each H2 should contain a **number, a currency
amount, a date, or a definition**.

An assistant lifts a short passage, usually the opening of the most relevant section. If that
opening is throat-clearing, the passage that gets quoted contains no facts, and a
competitor's page gets used instead.

### The test

Read only the first sentence of each section. Does it answer the heading?

| Section | Fluff-first | Answer-first |
|---|---|---|
| How much does it cost? | "Every project is different, and we believe in pricing that reflects the value we deliver." | "Projects start at £18,000. Retainers are £4,500 per month. Both are quoted fixed, not by the hour." |
| How long does it take? | "We know timing matters to our clients, and we pride ourselves on responsiveness." | "Median implementation is 11 working days from signature to first output." |
| Where are you? | "We are proud to serve customers across the region from our long-standing base." | "Our office is at 418 Harbour Street, Portland, OR 97204. Reception is open 09:00–17:00 Pacific." |

The persuasion is not deleted — it moves. Context works better *after* the fact than before
it, for human readers too.

### What is excluded

Navigational sections — "Related", "Read next", "Where to go next", "See also" — and any
section that is over 70% link text. Judging a related-links block for not opening with a
number would penalise exactly the wayfinding this marketplace recommends elsewhere.

---

## The four core facts

Each is either stated in plain text somewhere on the site, or it is not.

### 1. Price

State the figure. If you genuinely cannot:

> "Vellum Studio does not publish fixed prices. Projects are quoted after a scoping call,
> typically within five working days, and start at £18,000."

An explicit refusal is **quotable**. Silence is not. An assistant asked "how much does X
cost" can repeat the first and can say nothing about the second.

### 2. Location or service area

Full postal address in an `<address>` element on the contact page, repeated in the footer and
in `Organization.address`. No premises? State the service area instead:

> "Brightpath serves retail operations teams across the United States, the United Kingdom and
> Canada."

### 3. Contact method

An email address and a phone number **in text**, as `mailto:` and `tel:` links — not only
behind a form. A form is an action a machine cannot take on a visitor's behalf. Add the
response time; an answer time is itself a quotable fact.

### 4. Founding or team facts

> "Harrowgate Press was founded in Skipton in 2011 by Eileen Trask. Two editors, one designer
> and one part-time publicist work from the Otley Street office."

These are what distinguish you from another company with a similar name (mechanism D). They
are also the details that make a description feel specific rather than generic.

---

## Ambiguity traps

### Naming consistency

Pick one written form — capitalisation, spacing, punctuation — and use it everywhere. Declare
the others in `Organization.alternateName` so they are stated as the same entity rather than
inferred as different ones.

The check compares **only names the site asserts as its identity**: `Organization.name` and
`og:site_name`. Names guessed from a `<title>` are deliberately excluded, because
"Brand | Tagline" splits into a brand and a tagline and comparing them would fire this
finding on almost every site on the web.

### Sentence length

Over 25% of sentences exceeding 30 words is reported at `low`.

A quotable fact must survive being lifted out of its paragraph. A 40-word sentence carrying
three qualifications cannot be excerpted without changing its meaning, so it tends not to be
excerpted at all. One idea per sentence; keep the occasional long one for rhythm.

### Slogan headings

A heading using vocabulary the page never uses again — "Built to fly", "Dream bigger" — tells
a machine nothing about what follows.

The bar is deliberately high: over 80% of at least five judged headings. A good heading often
does *not* repeat itself in its own first sentence — "Starter plan" followed by "$480 per
month" is correct writing — and an earlier, stricter version of this check flagged exactly
that. Headings made entirely of short or common words ("Who it is for") are skipped rather
than counted against the page.

---

## An FAQ block built from the site's own headings

The cheapest large win available to most sites. Take the questions your sales or support team
actually answers, phrase them as customers ask them, and answer each in two or three
sentences leading with the value.

```html
<h2>How much does <product> cost?</h2>
<p><strong><Product> costs $480 per month on the Starter plan and $1,450 on Growth.</strong>
Both include daily delivery and email support. There is no setup fee on any plan.</p>

<h2>How long does setup take?</h2>
<p><strong>Median setup is 11 working days from signature to first output.</strong>
Feed validation is the longest stage at 4 to 6 working days.</p>

<h2>Who is it not a good fit for?</h2>
<p><strong>It is not a fit for single-store retailers or businesses without a digital
inventory system</strong>, because forecasts need at least 12 months of SKU-level history.</p>
```

Then mark it up as `FAQPage` (see `structured-data-audit/references/jsonld-templates.md`),
with the answer text matching the page exactly.

That third heading is the one to keep. A page that says who it is *not* for is treated as a
more reliable source than one that claims to suit everybody — and it is a question people
genuinely ask assistants.
