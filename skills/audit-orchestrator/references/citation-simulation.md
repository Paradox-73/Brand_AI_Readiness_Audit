# Citation simulation

For each key page, the report answers one question: **if an assistant fetched this page to
answer a question about the brand, which sentence would it most likely quote?**

The answer is a real sentence from the page, or `Nothing quotable`.

Implemented in `compose_report.py::simulate_citations`; emitted as `citation_simulation[]`
and rendered as a table in `report.md`.

---

## Why this is in the report

"Your pages contain no extractable facts" is an abstract claim that a marketing manager can
read past. The same claim as a table, with the page's own words in it, is not:

| Page | Type | Likely quote |
|---|---|---|
| `/` | home | Brightpath Analytics is a demand-forecasting platform that cuts stockouts for mid-market retailers running 5 to 50 warehouses. |
| `/pricing` | pricing | Starter is $480 per month, Growth is $1,450 per month, and Enterprise starts at $3,900 per month. |
| `/faq` | faq | Brightpath Analytics costs $480 per month on the Starter plan, $1,450 per month on Growth, and from $3,900 on Enterprise. |

versus the same site's competitor:

| Page | Type | Likely quote |
|---|---|---|
| `/` | home | **Nothing quotable** — no sentence on this page both stands alone and states a fact |
| `/about` | about | **Nothing quotable** — no sentence on this page both stands alone and states a fact |
| `/pricing` | pricing | Design that has to be redone is the expensive option. |

Both tables are real output, from `tests/fixtures/good-site` and
`tests/fixtures/js-shell-site`. The second one makes the problem impossible to argue with,
and the third row is the sharpest part of it: the page *does* have a quotable sentence, and
it says nothing about what anything costs.

It also gives a monitoring baseline that needs no external API. Re-run after a rewrite and
the column changes.

---

## How the sentence is chosen

Candidates are drawn from the page's `<p>` elements, not from the flattened page text. This
matters: flattening runs an `<h1>` into the sentence after it, and "Demand forecasting for
mid-market retail warehouses Brightpath Analytics is a demand-forecasting platform..." is not
a sentence anyone would quote. Paragraph-scoped extraction was a correctness fix, not a
refinement.

Sentences are then tested in three passes, stopping at the first hit:

1. **A definition naming the brand.** Contains the brand name *and* a verb from
   `is / are / was / provides / offers / helps / builds / makes`. Ranked first because
   identity is what an assistant needs before it can say anything else about a brand, and it
   is the sentence most sites never write.
   → `why: "names the brand and says what it is"`

2. **A sentence stating a number.** Contains a digit or a currency mark, and is 40–300
   characters. Prices, dates, quantities, durations — the things people actually ask for.
   → `why: "states a number a reader could act on"`

3. **A sentence that defines something.** Matches `is/are a/an/the`, or
   `means / refers to / defined as`. Weaker, but still liftable.
   → `why: "defines something, though it states no figure"`

Nothing found → `likely_citation: null`, with the reason spelled out.

The 40-character floor rejects fragments; the 300-character ceiling rejects sentences too
long to excerpt without changing their meaning (the same reasoning behind
`fact-extractability-audit`'s long-sentences check).

Pages covered: home, about, pricing, faq, product, service, contact, location — capped at 12,
sorted by URL for determinism.

---

## What this is not

**It is not a prediction.** No assistant is queried. It is a deterministic reading of the
page under the same constraints an assistant works within — self-contained, factual,
liftable, near the top. Two different assistants would pick differently, and both might pick
something else entirely.

**A hit is not proof of success.** It says the page contains *something* liftable, not that
the sentence is the one the brand wants quoted, nor that any assistant will reach the page
at all. If gate A is shut, the best sentence in the world is never read.

**A miss is stronger evidence than a hit.** `Nothing quotable` means no sentence on the page
both stands alone and states a fact. That conclusion is safe regardless of which assistant
does the fetching, which is why the misses are the rows worth acting on.

This is deliberately listed as an optional extra rather than a finding: it produces no
severity, no priority and no `root_cause`. It exists to make the extractability findings
concrete.
