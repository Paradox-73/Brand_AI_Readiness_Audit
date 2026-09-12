# Proactive recommendations catalogue

Recommendations are things worth doing that are **not defects**. They live in
`recommendations[]`, never in `findings[]`, so they cannot inflate the severity counts.

Each one has a **condition** evaluated against what the audit actually observed. A
recommendation whose condition is not met is not emitted. That is the whole design: a
catalogue that always prints everything is a brochure, and a reader learns to skip it.

Implemented in `compose_report.py::build_recommendations`.

---

The **rests on an absence** column is the second gate, below.

| ID | Condition | Mechanism | Rests on an absence |
|---|---|---|---|
| `R-LLMS-TXT` | `/llms.txt` absent | B | no — one request to one URL at the site root, which answered |
| `R-FAQ-SCHEMA` | no FAQPage markup anywhere | B | **yes** |
| `R-SAMEAS-WIKIDATA` | fewer than 6 off-site profiles measured | D | **yes** — counted off the pages that were read |
| `R-BOILERPLATE` | always | D | no — there is no absence in it to be wrong about |
| `R-PRESS-PAGE` | no press page, the site could have a press office, and either a core fact is missing or the site contradicts itself about one | D | **yes** |
| `R-DATE-SIGNALS` | article pages carry no date signals | D | **when it fires on the absence half.** A `no-date-signal` or `stale-content` finding is a date that was read; "no date signals" and a low coverage share are counts over the pages the crawl reached |
| `R-COMPARISON-PAGES` | the site sells something, no comparison page was found, the site's own copy already compares what it sells with something else ("unlike…", "better than…"), and the site could be an online seller, local business, organisation or project | E | **yes** — the selling half and the quoted sentence are measured, the missing comparison page is not |
| `R-USE-CASE-PAGES` | 2+ location, product or service pages sharing one H1 and opening paragraph, a location page counting only where it prints a street address of its own, and the site could have commercial audiences | E | no — two such pages must have been read |
| `R-HTML-FOR-PDF` | a `pdf-locked-facts` finding was raised | C | no — a finding fired |
| `R-EMAIL-TEXT-FIRST` | a newsletter signup was detected | F | no — a form was found |
| `R-WAYFINDING` | a `dead-end` finding, or 2+ deep pages whose only onward links are the site's own menu | G | no — every branch names something read |
| `R-LANDING-PAGE-ANSWERS-FIRST` | 2+ landing pages state a price and answer nothing else a buyer needs, or bury the price below the fold | G | no — both signals name measured URLs |
| `R-BRAND-TERMINOLOGY` | 2+ pages that sell, nothing the site marks up names a property of its own, and the selling pages' copy marks at least one term other than the brand name with ™ or ® | B | **yes** — the markup half is an absence over whatever was crawled |

**Thirteen entries, and two were deleted rather than fixed.** `R-ANSWER-FIRST` and
`R-AUTHOR-PAGES` were retired under the rule at the foot of this file — a finding may
*trigger* a recommendation but may not be *restated* by one, and each of those two fired on a
signal that already raises a finding and then asked for that finding's own fix.
`R-WAYFINDING` lost the two steps doing the same. Deleting them is the change: advice any web
developer would have given without running this audit adds nothing, and what this catalogue
is for is the non-obvious.

---

## The coverage rule the findings already obey

An absence-conditioned entry may not instruct when the crawl did not read enough of the site
to establish the absence. The predicate is `compose_report.enough_to_grade` — the same one
the verdict uses and the same one `withhold_absence_claims` obeys for findings, so the two
halves of a report cannot disagree about whether the crawl saw enough.

**What it costs to skip this.** One crawl reached a single page of an education site. Its
findings section correctly held its absence claims back, and its recommendations section then
told the site to publish a press page and FAQ content. Both existed — `/about/press` and a
full support site, each answering 200. A reader who checks one such instruction and finds the
page already there stops reading, and the report loses every true entry underneath it.

**Reworded, not deleted.** Where `enough_to_grade` is false, an absence-conditioned entry
keeps its steps and gets:

- its title reworded as a question — "Publish a press page with reusable facts" becomes "Do
  you already publish a press page with reusable facts?";
- the coverage sentence in front of its summary, naming how many URLs were read of how many
  reached and why the rest were not;
- `conditioned_on: "absence"` in `recommendations[]`, so a consumer of the JSON can tell the
  two apart without parsing prose.

An absence-shaped recommendation is still worth putting to somebody who knows the site — they
can settle it in a second — and unlike a finding it accuses nobody of anything. What it may
not do is instruct.

`rests_on_absence` is declared per entry at the call site rather than inferred from the
wording, because several conditions are mixed: only the caller knows which half fired on this
run.

---

## The steps have to fit the kind of site

The findings had been gated on `audit_common.site_kind` for some time and this catalogue
never was. The suggested actions showed it: **"Corroborate the brand" read "Claim or update: LinkedIn company page, Google Business Profile,
Crunchbase" identically for a shoe shop, a municipal government and an open-source project
with no company and no premises.** Two of those three cannot claim a business listing at
all, and a step nobody can carry out is where a reader stops — which costs the report every
true entry underneath it.

Two calls, and which one to use is the whole of the interface:

- **`might_be(...)` gates.** It answers True for a site nothing was determined about and for
  a weak determination, so an unplaceable site gets exactly the catalogue it got before and
  a wrong guess can never delete advice. Only `R-PRESS-PAGE` and `R-USE-CASE-PAGES` are
  gated this way, and both go quiet only where the structure says there is no press office
  and no commercial audience.
- **`is_certainly(...)` chooses wording, never whether an entry fires.** Naming the wrong
  platform is a smaller failure than hiding an entry, so the default list stands wherever
  the classifier is quiet.

Platform lists are written as **things to go and claim** rather than as brand names — "the
package index people install it from", "the mapping service people search in" — so a step
stays followable by somebody who knows their own country's equivalent and stays true when a
platform is renamed. This is the pattern
`freshness-corroboration-audit::_how_to_widen_the_footprint` already uses for the fix text
of the finding `R-SAMEAS-WIKIDATA` pairs with, so the finding and the recommendation now say
the same thing to the same site.

Reworded by kind: `R-SAMEAS-WIKIDATA` (platforms, and the company-record step), `R-BOILERPLATE`
(what the paragraph says and where it is pasted), `R-PRESS-PAGE` (who the page is for and
which figures it carries), `R-FAQ-SCHEMA` (who answers the ten questions), `R-LLMS-TXT`
("a one-line brand definition" became "a one-line definition of what this is").

---

## Conditions have to be measurements, not page types

Three of the entries then in this catalogue fired on **six sites out of six**, and only two of
them read as non-obvious.
Every one of the three was gated on something being *present* rather than on something being
*measured* — a site having a product page at all, or not having a page this audit happens to
type as a comparison. A condition almost every site meets is "always" wearing a condition's
clothes, which is the brochure this file's own opening paragraph forbids.

What changed, and what did not:

- **`R-COMPARISON-PAGES`** now also requires `sells_something` — a product page, a pricing
  page, commerce markup, or a price on a page whose job is prices. It was telling a national
  weather service and a computing history archive to write "X vs Y" pages. Four of the six
  sites in that sample sell nothing. It then fired on five shops in one graded pass, word for
  word, because selling with no comparison page is every shop. So it now also needs a sentence
  in the site's own copy that already compares — `compose_report.comparative_claims` — and the
  entry quotes that sentence: a comparison that concedes nothing is the thing its steps fix.
- **`R-SAMEAS-WIKIDATA`** dropped its `or no Wikidata/Wikipedia anchor` clause. That clause
  is contradicted by [the study](cited-vs-uncited-study.md) two sections of this repository
  cite: the gap between brands assistants name and brands they ignore is *spread across
  ordinary platforms, not concentrated in Wikipedia or Crunchbase*. Almost no brand site
  links a Wikidata item, so the clause fired everywhere — including on two sites in that sample
  linking 8 and 10 profiles, both above the 7.2 mean measured for brands that do get named.
  The remaining condition, `profile_breadth < 6`, is the number that study produced and the
  same one the `weak-corroboration` finding passes at, so the finding and the recommendation
  can no longer disagree about who needs this.
- **`R-BOILERPLATE`** is deliberately unconditional and stays that way. Its section below
  says why, and the measurement that could have gated it is dealt with there.
- **`R-USE-CASE-PAGES`** now counts the pages instead of testing the set: two location,
  product or service pages, not one of any of them. This entry asks somebody to split a
  template that serves several audiences at once, and a site with one such page has no
  template to split. It is also `might_be`-gated, because "the three audiences or locations
  that matter commercially" is a sentence about a business. A page typed `location` counts
  only where it prints a street address of its own: the type is read off the URL path, and a
  language foundation's venue listings for other groups' meetings, at `/locations/<id>`
  with no address, met the two-page bar without being a place the site serves.
- **`R-BRAND-TERMINOLOGY`** is now on two measurements, neither of them a page type. **Two
  pages that sell**, asking `sells_something` of one page at a time so the definition cannot
  drift from the one the pricing checks use — on a six-page crawl of an open-source project
  a single stray figure was enough to make the whole site a seller. And **nothing the site
  marks up names a property of its own**: no `additionalProperty`, no `material`, no
  declared `PropertyValue`, matched on the JSON-LD keys rather than on the string appearing
  anywhere, so a product described as "made of recycled material" is prose and not a
  property. That second half is exactly what the entry's own third step asks for, so a site
  that has already taken this advice stops being told to take it. A third measurement came
  after five shops in one graded pass were told to "name your own components" whether or not
  their copy named one: **a term the selling pages mark with ™ or ®**, other than the brand
  itself (`compose_report.terms_the_copy_names`). That is a name the site has already chosen,
  and the entry quotes it.
- **`R-WAYFINDING`** now fires only on a `dead-end` finding or on 2+ deep pages whose only
  onward links are the site's own menu. A broken link, an orphan page, a missing breadcrumb
  and an off-site search box used to trigger it too; on a national library it rested on one
  dead `.opml` link while every one of its 24 deep pages carried a link of its own. A broken
  link is a link to fix and an orphan is a page nothing links to — neither is a page that ends.

Measured on thirteen real crawls, `R-PRESS-PAGE` went from 12 sites to 10 and
`R-USE-CASE-PAGES` from 7 to 6. `R-BRAND-TERMINOLOGY` stayed at 7 and changed which 7: off a
national weather service, on a clothing retailer whose category pages carry offer markup and
whose product pages were never crawled. A count that does not move is not the same as a
condition that does not work.

A condition on a measured list also has a floor: **two pages, not one**. One page is a page;
two independent pages showing the same gap is the template. The clean fixture — the one that
must produce nothing at all — has exactly one deep page whose only onward links are the
site's own menu, and a gate reading "the list is not empty" told that site to rebuild its
wayfinding.

---

## The reasoning behind the ones that are not obvious

### `R-BOILERPLATE` — the only unconditional entry

One paragraph of identity, reused **verbatim** across the site, `Organization.description`,
LinkedIn, the press kit, app store listings and every directory.

It is unconditional because identical wording across independent sources is the strongest
corroboration signal available (mechanism D) and it costs nothing but discipline. Every site
benefits, including a site with no defects at all — which is why `tests/fixtures/good-site`
produces zero findings and still receives this recommendation.

The one measurement available to gate it is `distinct_org_descriptions`, the count of
distinct `Organization.description` values the crawl read: 0 would mean the boilerplate does
not exist as data, 1 that it exists and is reused, 2 or more that two versions coexist. It
came back **0 on all fifteen real sites measured**, so gating on it would fire everywhere
and merely look measured, which is worse than an unconditional entry that says so. What did
change is the wording: "when you started" is a founding date a program does not have, "who
you serve" is customers a public body does not have, and the list of places to paste it
named a press kit and app store listings to sites with neither.

It is also the one entry the coverage rule leaves alone, because there is no absence in its
condition to be wrong about — but the places it names to paste the paragraph are now the
site's own profile links wherever the crawl read any, since a generic list of destinations
was wrong for every site in the last validation pass.

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

### `R-LANDING-PAGE-ANSWERS-FIRST` — mechanism G

The page an assistant deep-links to is not a homepage, and the person landing on it is not a
new visitor. They have already been told what this is; they clicked to settle one question,
and it is almost always what it costs, whether it is in stock, or when it would arrive. A
template that opens with the brand story is answering a question they stopped asking three
steps ago, so they go back and ask the assistant again.

The condition is two measurements of the pages themselves, never a page type:
`landing_pages_stating_only_a_price` names the pages that publish a price and answer none of
the three questions left after it, and `landing_pages_that_bury_the_price` names the pages
that answer it further down than a visitor reads. Both are lists of URLs the engagement
checks measured, so this cannot fire on a site nobody measured: a crawl too small to
describe a template emits empty lists and this stays quiet.

It is the cheapest entry in the catalogue, because every block it asks for already exists on
the page — the change is which order they come in — and the markup half is three properties
on an `Offer` node most shops already publish.

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

Its first step is the whole of it: **the thing that makes a product different has to be a
declared property rather than an adjective**, or it is discarded with the rest of the copy. An
assistant asked to compare two products reads properties. "Hand-finished in small batches" is
copy; `additionalProperty` with a name and a value is a fact that survives extraction, and it
is the same sentence either way.

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

---

## The two rules the table is enforced against, entry by entry

1. **The condition is a measurement and never a page type.** `engagement-audit` emits four
   measurements — how many landing pages were judged for purchase facts, how many state only a
   price, how many bury it, how many deep pages offer only site-wide links — precisely so the
   entries above can be gated on something a site can pass rather than on something it has.
2. **A finding may *trigger* an entry but may not be *restated* by one.** This is the rule
   that deleted `R-ANSWER-FIRST` and `R-AUTHOR-PAGES` and cut two steps out of
   `R-WAYFINDING`, and deleting is what enforcing it looks like: advice any web developer
   would have given without running this audit adds nothing, and what this catalogue is for
   is the non-obvious.
