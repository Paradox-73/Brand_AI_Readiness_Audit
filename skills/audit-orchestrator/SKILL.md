---
name: audit-orchestrator
description: Audit any website for why an AI assistant cannot find, read, trust or correctly describe the brand, and why visitors who do arrive leave again. Crawls the site once, runs six specialist sub-skills over that one snapshot, and emits a prioritised report of evidence-backed findings with concrete fixes plus proactive recommendations. Use this when someone asks why their brand is missing from ChatGPT, Perplexity, Claude or Google AI answers, why an assistant describes them wrongly or out of date, why their site is invisible to crawlers, or why traffic arrives and bounces. This is the entrypoint for the brand-ai-readiness-audit marketplace and composes every other skill in it.
license: MIT
compatibility: Requires Python 3.10+ with requests, beautifulsoup4 and lxml, and outbound network access to the audited site. Playwright is optional and enables a rendered-DOM comparison.
allowed-tools: Bash(python3:*) Bash(python:*) Read Write WebFetch
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "A B C D E F G"
  role: "entrypoint"
---

# Brand AI-readiness audit (discoverability + engagement)

## When to use

Use this skill when someone gives you a website and asks any version of:

- "Why doesn't our brand show up when I ask ChatGPT about our category?"
- "Why does the assistant describe us with old prices, an old logo, or the wrong company?"
- "Why do people click through from an AI answer and immediately leave?"
- "Audit this site for AI discoverability."

Do not use it to change a website. This marketplace only reads and recommends.

## Inputs

- **Required:** a URL or bare hostname (`example.com`, `https://example.com/some/page`).
  A path is kept as a seed page, but the audit is of the whole origin.
- **Optional:** `--render` to add a Playwright pass, `--no-network` to suppress every
  request beyond the crawl, `--now YYYY-MM-DD` to fix the reference date for freshness
  checks, `--format html` for a shareable report.

## Procedure

Run `python3 run_audit.py <url>` to do all of this at once, or follow the steps directly.

1. **Normalise the target.** Reduce the input to an origin (`https://example.com`).
   Keep any supplied path as an extra seed page.

2. **Crawl once.** Run `scripts/crawl.py <origin> --out snapshot.json --budget 115` — not the
   240 default, because step 3's six skills are owed a share of the same five minutes.

   It fetches robots.txt, the sitemaps, `/llms.txt`, the homepage, up to 8 sitemap URLs
   sampled with seed 42 and spread across detected page types, and then breadth-first from the
   homepage to depth 2. Hard caps: 60 pages, the wall clock it is given, 10 s per request, 30 s
   total per request, 5 MB read per response, 0.5 s between requests, single threaded.

   Only pages are fetched. An ending the crawl recognises as never a page costs no request at
   all; every other URL costs one, and its body is read only if the content type says it is a
   page. A response that was never a page spends no page slot and leaves every count of how
   much of the site was read.

   Where Playwright is installed the crawl also renders a spread of five pages and re-reads any
   whose delivered HTML holds almost no text, so the other five skills judge the page a visitor
   sees. A thin page the browser never reached carries `render_skipped`, and **no check may
   assert a page-level absence about one.**

   Two conditions stop the run: a homepage that does not respond after two attempts (one
   critical finding — every other check is meaningless), and a robots.txt that disallows this
   auditor at `/` (no pages fetched at all; report the access findings and mark the rest not
   applicable).

   `references/crawl-mechanics.md` has the rest: why each cap is the number it is, what the
   crawl measures once so no later check pays for it again, and the exact boundary of the
   read-only promise.

3. **Run the six sub-skills in this order**, each as
   `python3 skills/<skill>/scripts/check.py --snapshot snapshot.json --out <skill>.findings.json`:

   1. `crawl-access-audit` — is the crawler let in?
   2. `render-readability-audit` — can a machine read what is delivered?
   3. `structured-data-audit` — are the facts machine-readable?
   4. `fact-extractability-audit` — is there a sentence worth quoting?
   5. `freshness-corroboration-audit` — is it current, corroborated and unambiguous?
   6. `engagement-audit` — does an arriving visitor stay?

   The order matters. When two skills observe the same root cause on the same pages, the
   earlier one owns the finding and the later one's evidence is merged into it.

4. **Compose.** Run `scripts/compose_report.py --snapshot snapshot.json --findings <all six>
   --out-json report.json --out-md report.md`. This deduplicates, assigns stable IDs, scores
   priority, simulates which sentence an assistant would quote from each key page, and
   appends only those proactive recommendations whose conditions were actually met.

5. **Return `report.json` and `report.md`.** Lead your answer with the verdict line and the
   three "start here" fixes. Do not re-summarise every finding in chat; the report is the
   deliverable. Where `crawl.enough_to_judge` is `false`, lead instead with what the crawl
   could not establish — the verdict already says it — and do not quote a severity count as
   though it were a result.

### Doing this without Python

Every sub-skill's SKILL.md names its checks and points at the reference file carrying each one
in full. Fetch the same pages by hand (GET only, respecting robots.txt), work through each
skill's procedure, and record findings in the shape described in
`references/report-schema.json`. You will reach the same findings; the scripts are the
executable form of the same logic, not a different method.

## Output

`report.json` — the required schema (`site`, `audited_at`, `summary`, `findings[]` with
`id`, `title`, `severity`, `evidence`, `suggested_action{summary, priority}`) plus
extensions: `confidence`, `evidence_basis`, `tier`, `mechanism`, `root_cause`,
`affected_pages`, `reach`, `detected_by`, `suggested_action.effort`, `.owner`,
`.how_to_fix[]`, `.snippet`, `.rationale`, and top-level `recommendations[]`,
`citation_simulation[]`, `start_here[]`, `worth_checking[]`, `crawl{}`, `checks_run[]`,
`not_applicable[]`, `checks_with_nothing_to_run_against[]`, `unverifiable[]`.

`report.md` — written for a marketing manager: a plain-language verdict, the top three
fixes, findings grouped by severity with evidence and paste-ready snippets, the lower tier
under "Worth checking first", the recommendations, what an assistant would quote from each
key page, and an appendix accounting for every check.

Two states outrank the grades and are printed above the severity table in both documents.
`crawl.origin_redirect` with `followed: false` means no page of the address somebody typed
was fetched; `crawl.site_not_serving` means every address the crawl reached answered the
same service-level status (402, 410, 451, 503), so the site is declining to serve rather
than missing a page. Either forces `crawl.enough_to_judge` to false: such a snapshot cannot
support a verdict of any kind, a clean one included. A check registered but never reached
then goes in `checks_with_nothing_to_run_against[]` rather than among the checks that ran
clean, and a whole sub-skill that did not run is named in `skills_that_did_not_run[]` under
"Questions this audit could not answer".

### How a finding is ranked, tiered and chosen

Priority is `severity weight x reach / effort`, not severity alone, so a cheap site-wide fix
outranks an expensive one affecting a corner.

Each finding also carries `evidence_basis`: `observed` where the claim is settled by what the
document or the HTTP exchange literally contains, `inferred` where it also rests on this audit
having decided what something *is*. `tier` is `confident` only for an `observed` finding at
high confidence, or at medium with two independent sources in `checked[]`; everything else is
`worth checking`, printed under a heading telling the reader to verify it first and never
eligible for "Start here". Demotion is not deletion — a demoted finding keeps its severity, its
place in the counts and its row in `findings[]`.

"Start here" is three fixes in the order to do them, drawn from the confident tier, except that
a root cause the verdict names as the thing most of the rest follows from leads the list
whichever tier it is in. One line under the verdict counts how many of the actions need
somebody who edits the files the site is built from rather than typing into an admin screen.

`references/severity-and-priority.md` has all of it: the weights, the measured accuracy of both
tiers on two validation samples, the two causes exempt from the second-source floor, and what
those numbers do and do not license.

### One coverage rule, deciding both what may be asserted and what may be graded

`crawl.readable_pages` counts pages that answered 200, were not skipped or challenged, and
carried at least one sentence of text. `compose_report.enough_to_grade` asks two things of it:
at least `VERDICT_MIN_READABLE_PAGES` of them, and at least `READABLE_SHARE_FOR_ABSENCE` of the
URLs that could have been pages. Fail either and the verdict states what the crawl did and did
not establish instead of grading the site, above the severity table in both documents. Where
nothing readable came back, every check that stayed quiet is echoed into `unverifiable[]` so
its silence is not read as a pass.

The two conditions are one predicate on purpose. A verdict computed from severity counts alone
twice told an owner "No blocking or significant problems were found" on a run that read no text
at all; and when the two were separate, a crawl of sixteen URLs with seven readable held back
eight true findings for want of coverage and printed "No blocking problems." in the same report.

### Beyond the defects

Recommendations are not repairs. Each has a condition evaluated against what the audit
observed, and only the ones the crawl met are printed, so a site that already does a thing is
never told to do it. The catalogue runs from publishing `/llms.txt` and real `FAQPage` markup,
through claiming and linking the profiles that corroborate you and reusing one canonical
paragraph verbatim across them, to a press page, dated content, comparison and use-case pages,
an HTML version of anything that exists only as a PDF, text-first email, wayfinding on deep
pages, and a landing template reordered around the question the visitor arrived with.

Two rules govern the table, enforced entry by entry in
`references/proactive-recommendations.md`: the condition is a measurement and never a page
type, and a finding may *trigger* an entry but may not be *restated* by one. Two entries were
retired against the second and a third lost two steps — advice any web developer would have
given without running this audit adds nothing, and what this catalogue is for is the
non-obvious.

**The coverage rule above governs these too.** Most of the catalogue is absence-shaped, so when
`enough_to_grade` is false those absences are facts about the crawl; such an entry is reworded
as a question with the coverage sentence in front of it rather than deleted. One report reached
a single page of a site and recommended publishing a press page and FAQ content that site had
had for years.

## Not applicable when

- The target is not reachable over HTTP, or is behind a login. This audit never
  authenticates.
- The user wants changes applied. Every skill here is read-only and recommends only.
- The user wants a competitor comparison or keyword ranking report. This audits one site's
  machine-readability and on-site experience, not its market position.
- robots.txt disallows this auditor. In that case the run stops and says so rather than
  ignoring the file.

## Safety

GET and HEAD only. No forms submitted, no cookies sent that the site did not set, nothing
fetched under `/cart`, `/checkout`, `/login`, `/account` or `/admin`, and nothing robots.txt
disallows. A page that *redirects* to one of those is dropped as well. The auditor identifies
itself as `BrandAIReadinessAudit/1.0 (+read-only audit)`.

There is exactly one exception to the user-agent rule and two third-party services, all three
declared: the AI-user-agent comparison probe in `crawl-access-audit` (skipped where robots.txt
disallows the agent, three requests at most), and `freshness-corroboration-audit`'s profile-link
and Wikidata lookups. Each profile link is a page fetch, so that host's own robots.txt is read
first and a platform that disallows automated requests is reported as unchecked rather than
probed. The Wikidata lookup uses the public JSON API Wikimedia asks bots to use in place of
crawling wiki pages. `references/crawl-mechanics.md` states each boundary in full.

### Claims of absence

A finding whose root cause asserts a negative — no Organization markup, no definition sentence,
no breadcrumb, no date signal — must name the independent sources it consulted, and is capped at
medium confidence when it rests on one of them. `make_finding` refuses the call without them and
the report prints the list as *Where we looked*.

This is the rule that keeps a detector's blind spot from being published as a fact about the
site. A breadcrumb the site names only in a `data-` attribute, a call to action that is a
`<button>`, an FAQ with no markup: in each case the page had the thing and one detector did not
know that shape. Working by hand, do the same — say where you looked, and consult a second place
before stating at high confidence that something is not there.

## References

- `references/mechanism-model.md` — the seven mechanisms every check traces back to.
- `references/crawl-mechanics.md` — why each crawl cap is the number it is, what the crawl
  measures once so no later check pays for it again, and the read-only promise in full.
- `references/cited-vs-uncited-study.md` — what separates sites assistants cite from sites
  they ignore: 36 matched-pair sites, then three holdout samples of 24, 32 and 40 more, each
  predicted in writing before it was run. Includes the checks that did not separate them.
- `references/how-the-checks-were-built.md` — the nine methods behind the checks, the defects
  each one caught that the others could not see, and the eight rules that replaced sixty
  one-at-a-time patches.
- `references/round2-failure-modes.md` — every one of the seven failure signals mapped to the
  check ids covering it, all 96 of them, then the four Round 2 failure modes in full.
- `references/severity-and-priority.md` — how severity, reach, effort and priority are set,
  how the two tiers are decided, and what "Start here" may and may not name.
- `references/proactive-recommendations.md` — the catalogue and each entry's trigger condition.
- `references/name-forms.md` — which strings may be offered as this site's own name, and the
  two opposite failures that made one list unable to serve both purposes.
- `references/publishing-platform.md` — how the audit works out what a site is built with, so
  a fix step can name the file or the screen rather than "the site-wide template".
- `references/report-schema.json` — the output contract.
- `references/citation-simulation.md` — how the "what would an assistant quote" column works.
