# Decisions and assumptions

Every judgement call made while building this marketplace, with the reasoning. Where a
decision departs from the brief or from a source of truth, that is said outright rather than
buried.

---

## 1. Agent Skills specification deltas

The live specification at `agentskills.io/specification` was fetched and checked before any
`SKILL.md` was written. Four things differ from, or extend, the brief:

| Point | Brief | Live spec | What was done |
|---|---|---|---|
| `allowed-tools` | listed as `Bash(python3:*) Read WebFetch` | *"A space-separated string"* | Emitted as a plain string, not a YAML list. `validate_marketplace.py` rejects a list. |
| `compatibility` | not mentioned | valid optional field, max 500 chars | Adopted on all seven skills to declare Python version, dependencies and network needs. |
| `metadata` | `version`, `author`, `mechanism` | *"a map from string keys to string values"* | All values quoted as strings, including `version: "1.0.0"`. A bare `1.0` would be a float and invalid. The validator checks this. |
| Body length | "under 300 lines" | *"Keep your main SKILL.md under 500 lines"* | Validator enforces the spec's 500; the project convention holds every body under 300, which the test suite asserts. |

Also confirmed and enforced: `name` is 1–64 chars, lowercase alphanumeric and hyphens, no
leading, trailing or **consecutive** hyphens, and must match the parent directory name;
`description` is 1–1024 chars.

`license` is *optional* in the live spec but the brief requires it, so it is required here.

Validated two ways: `skills-ref` (the official reference library, installed via
`pip install skills-ref`) passes on all seven folders, and `validate_marketplace.py` adds the
marketplace-level rules the spec does not cover — exactly one entrypoint, manifest/folder/
frontmatter name agreement, no path escaping the root, and no undeclared skill folder.

---

## 2. Mechanism G is an addition

The brief's mechanism model runs A–F. All six describe how *machines* find, read and repeat
content. None describes what happens after a person clicks through — yet Round 2's third
symptom, bouncing, is exactly that.

Forcing engagement findings into E would misdescribe them: **E** is how the *assistant*
personalises; the engagement problem is what the *visitor* arrives holding and the site
cannot see. So `G` was added:

> **G.** A visitor arriving mid-journey from an answer carries context the site never sees,
> and leaves unless the page orients them and offers a next step.

`engagement-audit` uses it. It is documented in `mechanism-model.md` and marked there as this
marketplace's own extension, not part of the brief's model.

---

## 3. GPTBot is classified as an answer crawler

`crawl-access-audit` splits AI crawlers into those whose blocking costs **citations** and
those whose blocking is a **rights decision about training data**. GPTBot is genuinely
ambiguous: OpenAI documents it primarily as the crawler whose content may improve models,
while `OAI-SearchBot` and `ChatGPT-User` are the retrieval-side agents.

It sits in the answer group, following the brief, because the cost of being wrong is
asymmetric. Calling it training-only risks staying silent about a block that suppresses the
brand — the exact failure this audit exists to prevent. Calling it an answer crawler produces
a `high` finding whose fix ("decide which of these you want quoting your pages") is
reasonable either way.

The finding text never claims GPTBot is purely a search crawler, and
`ai-crawler-user-agents.md` sets out the ambiguity in full. That file also warns that
operators change these tokens without notice.

---

## 4. The Round 2 "intent-driven landing pages" fix was not adopted

Round 2 proposed reading URL query parameters mapped from AI citations and reordering the DOM
to foreground the cited product. Not implemented as a recommendation, for two reasons:

1. **It usually would not fire.** Assistants frequently cite a bare canonical URL and strip
   tracking parameters, so the intent signal often never arrives.
2. **It edges toward cloaking.** Serving materially different content by parameter means the
   crawler and the visitor see different pages — the failure this entire audit exists to
   catch.

The durable version of the same insight is recommended instead: make *every* page
self-orienting so a visitor landing anywhere is oriented without the site needing to know how
they arrived, and serve genuine intent segments as real, crawlable, separately-addressable
pages (`R-USE-CASE-PAGES`, `R-COMPARISON-PAGES`), which also covers mechanism E.

Recorded in `round2-failure-modes.md` so the divergence is visible rather than silent.

---

## 5. Report schema extensions

The required shape is emitted exactly. Additions:

- **`info` severity.** Some observations are worth reporting and are not defects — blocking
  training crawlers being the clearest. `info` items appear in `findings[]`, are counted
  separately in `summary.info`, and are excluded from `start_here`.
- **`total_findings` includes `info`.** It is the length of `findings[]`, which is the least
  surprising reading. The per-severity counts let anyone subtract.
- **`suggested_action.priority` stays a string**, as the required schema specifies, and
  `priority_score` carries the number alongside. Sorting exactly needs a float; the required
  field needed a label. Both are emitted rather than overloading one.
- **`recommendations[]` is separate from `findings[]`**, so proactive advice can never
  inflate the defect counts.

---

## 6. Engineering choices

**A shared library, not six copies.** `audit_common.py` lives in the orchestrator's
`scripts/` and defines the finding schema, severity scale, root-cause vocabulary, page-type
detector and HTTP client once. Sub-skills reach it by resolving a path relative to `__file__`.

The alternative — a self-contained copy per skill — would satisfy folder independence and
guarantee drift. The spec requires each folder to hold a valid `SKILL.md`, not to duplicate
code, and `test_marketplace.py` asserts every emitted `root_cause` is in the shared frozen
set, so an invented tag fails loudly instead of silently failing to deduplicate.

**Sub-skills run as subprocesses**, in tests as in production, so the command-line contract
is exercised rather than assumed.

**Six sub-skills, not the handout's three.** The handout's layout is explicitly illustrative.
Its example shows one `crawl-render-audit`; that is split here into `crawl-access-audit`
("was the crawler let in") and `render-readability-audit` ("could it read what arrived")
because they are different mechanism gates with different fixes and different owners.

**`--now` for freshness.** Staleness thresholds are relative to a reference date. Without a
fixed one, "older than 18 months" changes meaning as the calendar moves and the test suite
would start failing on its own. Defaults to today, UTC; the suite pins `2026-09-01`.

**Default index documents fold to `/`.** `normalise_url` maps `/index.html`, `/default.aspx`
and similar to `/`. Without it the crawler spent budget fetching `/` and `/index.html` as two
pages and reported duplicate findings on both.

**Deduplication merges only across skills.** One skill emitting several findings that share a
root cause is doing so deliberately — robots.txt can block answer crawlers, block training
crawlers, and disallow content paths, which are three decisions with three fixes. An earlier
version keyed only on `(mechanism, root_cause, affected_pages)` and silently collapsed all
three into one.

**`WebSearch` is declared but never called from Python.** `freshness-corroboration-audit`
declares it in `allowed-tools` because an agent runtime may offer it, and records
`signals.suggested_web_search` — the query, not scraped results. A query is reproducible and
attributable; a scraped snippet is neither. Wikidata's public API is the only third-party
service actually contacted.

**Extra-request budgets are declared and tested:** crawl-access 10, freshness 4, engagement
20, the other three 0. `test_extra_requests_stay_within_declared_budgets` holds each skill to
the number its `SKILL.md` states.

---

## 7. Testing choices

**A custom fixture server.** `python -m http.server` cannot rewrite `{{BASE}}` into an
ephemeral port, return 403 to a named bot user agent, or emit an `X-Robots-Tag` header — and
the bot-manager and noindex checks cannot be tested without those.

**Golden files record root causes, not byte-exact findings.** Findings contain URLs and the
server binds an ephemeral port, so a byte-exact golden would encode the port number and fail
on every run. Root causes are the stable, meaningful assertion.

**`must_not_find` is as long as `must_find`.** A check that fires everywhere detects nothing,
so every fixture asserts what must stay quiet as well as what must fire.

---

## 8. False positives found by the clean-site fixture

`tests/fixtures/good-site` must produce **zero** findings. Reaching zero surfaced seven real
bugs, each of which would have fired on ordinary well-built sites:

1. **Price comparison was string-based**, so JSON-LD `480.00` and a page showing `$480` were
   reported as a contradiction. Now compared numerically.
2. **"Founded in 2019" was scored as a staleness claim.** The `as of <year>` pattern matched a
   bare `in <year>`, turning every about page into a finding. Now matches only phrases
   asserting currency.
3. **Related-links blocks were judged for not opening with a number**, penalising exactly the
   wayfinding this marketplace recommends. Navigational sections are now excluded.
4. **Taglines were treated as brand-name variants.** `<title>` splits into "Brand | Tagline",
   and comparing both would fire a naming-inconsistency finding on nearly every site. Only
   names the site *asserts* as its identity are compared.
5. **Slogan-heading detection compared headings to their own first paragraph.** "Starter plan"
   followed by "$480 per month" is correct writing and was flagged. Now compared against the
   whole page, at a much higher threshold.
6. **CTAs were matched against a fixed phrase list**, missing "Browse the range" and "Speak to
   the team". Now recognised by shape: an imperative opening verb or button markup.
7. **Site-chrome consistency took the plain majority signature**, so it bailed out on the worst
   case — a site where most pages have no navigation, making "no chrome" the mode. Now
   compares against the most common non-empty signature.

Two further bugs came from the broken fixtures: `statistics.median` raised on an even-length
list of dates, and the deduplication key collapsed three distinct robots.txt findings from
one skill into one.

---

## 9. Documentation follows Simplified Technical English

`README.md` and `PROGRESS.md` follow ASD-STE100, the controlled-English standard used for
aerospace and defence maintenance documentation. The rules applied are: short sentences,
active voice, simple tenses, one meaning per word, one instruction per sentence, no noun
clusters longer than three words, and articles kept in place.

The reason is the audience. Those two documents are read by teammates and judges who are not
inside the code, and `report.md` is read by a marketing manager. Controlled English is
measurably faster to read and harder to misread, and it removes the padding that makes
generated prose recognisable.

Two documents deliberately do **not** follow it. `DECISIONS.md` and the twelve files under
`references/` are written for an engineer who needs the reasoning, and the standard's
restricted vocabulary makes conditional argument awkward to express. The `SKILL.md` bodies
sit between the two: imperative, short-stepped and mostly compliant, because an agent reads
them as instructions.

---

## 10. Known limitations

Stated in `README.md` too, repeated here because they are decisions, not oversights:

- **30-page crawl budget.** On a large site this is a sample. The report says how many pages
  it saw; findings describe what was crawled, not what exists.
- **Static-first.** Without Playwright the render gap is inferred rather than measured, and
  the homepage-shell finding drops to `medium` confidence rather than pretending certainty.
- **No authentication**, ever. Anything behind a login is invisible.
- **Corroboration is one-sided.** The audit reads the brand's own site and counts Wikidata
  collisions. It cannot see what directories and aggregators currently say, which is the
  other half of Round 2's staleness diagnosis.
- **Engagement is inferred from markup, not analytics.** It finds the structural reasons a
  visitor would leave; it cannot tell you that they did.
