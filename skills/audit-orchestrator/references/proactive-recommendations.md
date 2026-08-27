# Proactive recommendations catalogue

Recommendations are things worth doing that are **not defects**. They live in
`recommendations[]`, never in `findings[]`, so they cannot inflate the severity counts.

Each one has a **condition** evaluated against what the audit actually observed. A
recommendation whose condition is not met is not emitted. That is the whole design: a
catalogue that always prints everything is a brochure, and a reader learns to skip it.

Implemented in `compose_report.py::build_recommendations`.

---

| ID | Condition | Mechanism |
|---|---|---|
| `R-LLMS-TXT` | `/llms.txt` absent | B |
| `R-ANSWER-FIRST` | fact-extractability found fluff-first sections | B |
| `R-FAQ-SCHEMA` | no FAQPage markup anywhere | B |
| `R-SAMEAS-WIKIDATA` | under 3 authoritative profiles, or no Wikidata/Wikipedia anchor | D |
| `R-BOILERPLATE` | always | D |
| `R-PRESS-PAGE` | no press page detected | D |
| `R-DATE-SIGNALS` | article pages carry no date signals | D |
| `R-COMPARISON-PAGES` | no comparison-type pages detected | E |
| `R-USE-CASE-PAGES` | location, product or service pages detected | E |
| `R-HTML-FOR-PDF` | a `pdf-locked-facts` finding was raised | C |
| `R-EMAIL-TEXT-FIRST` | a newsletter signup was detected | F |
| `R-WAYFINDING` | any dead-end, orphan, breadcrumb or broken-link finding | G |
| `R-BRAND-TERMINOLOGY` | product, service or pricing pages detected | B |
| `R-AUTHOR-PAGES` | article pages exist without author markup | D |

---

## The reasoning behind the ones that are not obvious

### `R-BOILERPLATE` — the only unconditional entry

One paragraph of identity, reused **verbatim** across the site, `Organization.description`,
LinkedIn, the press kit, app store listings and every directory.

It is unconditional because identical wording across independent sources is the strongest
corroboration signal available (mechanism D) and it costs nothing but discipline. Every site
benefits, including a site with no defects at all — which is why `tests/fixtures/good-site`
produces zero findings and still receives this recommendation.

### `R-LLMS-TXT`

A plain-text file at the root summarising the brand, its key pages and its canonical facts.
It hands a machine the summary you want quoted, instead of leaving it to assemble one from
whichever page it happened to fetch.

Worth being straight about: `llms.txt` is a proposed convention, not a standard, and support
is inconsistent. It is cheap, it is low-risk, and the exercise of writing one forces a brand
to decide what its canonical facts actually are — which is valuable even if no consumer ever
reads the file. It is recommended on those terms, not as a guaranteed win.

### `R-COMPARISON-PAGES` and `R-USE-CASE-PAGES` — mechanism E

Assistants weight answers toward the asker's context, so a brand with content for only one
framing is surfaced for only that framing. It is absent from every "X vs Y" question and
every "best X for &lt;use case&gt;" question regardless of how good its single page is.

The instruction to concede — "say plainly who each option suits better" — is not politeness.
A comparison page that never concedes anything reads as marketing and is discounted as a
source, so the honest version outperforms the flattering one.

These are recommendations rather than checks because there is no way to look at a site and
call the absence of a comparison page a *bug*.

### `R-EMAIL-TEXT-FIRST` — mechanism F

Only emitted when a newsletter signup exists, because otherwise it is advice about a channel
the brand does not run. Inbox summaries are built from message text: substance carried in an
image, or buried under filler, is absent from the summary the recipient actually reads.

### `R-BRAND-TERMINOLOGY`

Comes directly from the Round 2 "loss of brand voice" analysis (see
`round2-failure-modes.md`). Assistants extract explicit factual entities and discard the
copy around them, so brands come back flattened into generic specifications. A named,
defined component is a *fact* rather than an adjective, so it survives extraction and the
brand's own vocabulary has to be used.

The "coin sparingly" step matters: three defended terms beat twenty, and a page of invented
words with no definitions reads as noise and gets discarded wholesale.

### `R-AUTHOR-PAGES`

Attribution is a trust signal a machine can verify. Anonymous content competes badly against
the same claim made by a named, credentialed person. The condition requires article pages to
actually exist, so a brochure site is never told to hire writers.

---

## Rules for adding a new recommendation

1. **It must have a condition** other than `always`, unless it is genuinely universal and you
   can defend that in one sentence. There is currently one such entry.
2. **It must name a mechanism letter.** If it does not trace to the model, it is an opinion,
   and this catalogue is not for opinions.
3. **`why_this_works` must state the mechanism, not the benefit.** "Improves SEO" is not a
   reason. "Assistants lift short passages from near the top of a page, so a block written to
   be lifted is the passage they find" is.
4. **The steps must be executable by the named owner.** If a step needs a decision nobody has
   made yet, say what the decision is.
5. **It must not duplicate a finding.** If the audit can detect the problem, it belongs in
   `findings[]` with a severity. Recommendations are for what cannot be called a defect.
