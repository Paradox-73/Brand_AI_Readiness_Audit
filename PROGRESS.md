# Build Status
### Brand AI-Readiness Audit · Round 3

![Status](https://img.shields.io/badge/Status-Submission%20ready-2EA043?style=flat-square)
![Tests](https://img.shields.io/badge/Tests-169%20passing-2EA043?style=flat-square)
![Validators](https://img.shields.io/badge/Validators-2%20passing-0F9D58?style=flat-square)
![Package](https://img.shields.io/badge/Zip-0.26%20MB%20of%2050%20MB-4169E1?style=flat-square)

---

## Table of Contents

1. [Where We Are](#1-where-we-are)
2. [What Is Built](#2-what-is-built)
3. [How We Tested It](#3-how-we-tested-it)
4. [Real-Site Results](#4-real-site-results)
5. [False Positives We Found and Fixed](#5-false-positives-we-found-and-fixed)
6. [Open Items](#6-open-items)
7. [Where to Read More](#7-where-to-read-more)

---

## 1. Where We Are

The marketplace is complete and ready to submit. Seven skills, 169 tests, two validators
passing, and a 0.26 MB zip against a 50 MB limit.

| Gate | Result |
|---|---|
| Test suite | 169 pass in 62 s |
| `skills-ref` (official validator) | 7 of 7 skills pass |
| `validate_marketplace.py` (ours) | Manifest and all `SKILL.md` files consistent |
| Zero-findings guard on a clean site | 0 findings |
| Real-site sanity run | 7 public sites, 11 false positives found and fixed |
| Install and run from the zip | Works in a fresh virtual environment |

---

## 2. What Is Built

| Component | Detail | State |
|---|---|---|
| Skills | 7 (`audit-orchestrator` plus 6 sub-skills) | Done |
| Checks | 51 named checks across the six gates | Done |
| Reference documents | 12, one per `## References` section | Done |
| Shared library | Finding schema, root-cause vocabulary, page-type detection, HTTP client | Done |
| Crawler | Fixed budget, seeded sampling, robots-aware | Done |
| Report composer | Merge, priority, citation simulation, recommendations | Done |
| Output formats | `report.json`, `report.md`, optional `report.html` | Done |
| Test sites | 6 local sites, one per failure mode | Done |
| Test suite | 169 tests over behaviour, schema, determinism, safety, format | Done |
| Packaging | `package.py` builds and verifies the zip | Done |
| Documentation | README, DECISIONS, VERIFICATION, evals | Done |

The design decision that shapes everything else: **one crawl, six readers**. The
orchestrator fetches the site once and writes `snapshot.json`. Each sub-skill reads fields
off that snapshot rather than fetching anything itself. That is what keeps six skills from
becoming six crawlers, and it is why a full audit of a small site finishes in about ten
seconds.

---

## 3. How We Tested It

We built six websites, each carrying a different fault, and a small server that can do what
`python -m http.server` cannot: rewrite URLs into an ephemeral port, return 403 to a named
bot, and set response headers such as `X-Robots-Tag`. Without those three things the
bot-manager and `noindex` checks are untestable.

| Site | What it isolates |
|---|---|
| `good-site` | Nothing. It is built correctly and must produce zero findings |
| `js-shell-site` | Empty framework shells, facts in images, a pricing PDF, an iframed form |
| `blocked-site` | robots.txt blocks, a WAF returning 403, `noindex`, a dead sitemap URL |
| `no-schema-site` | Good prose, no structured data, one unparseable JSON-LD block |
| `stale-site` | 2019 copyright, old articles, a site that contradicts its own contact details |
| `dead-end-site` | "Welcome" H1, one nav item, orphan pages, broken links, a 12-field form |

`good-site` is the one that matters most. It is built to do everything this marketplace
recommends, so any finding it produces is a bug in a check rather than a fault on the site.
Driving it to zero is what exposed seven real defects, including a price comparison that
read `480.00` in the markup and `$480` on the page as a contradiction because it compared
the two as strings.

Golden files record which **root causes** should fire, not exact text. The test server binds
a random port, so exact text would encode a port number and fail on the next run. Each
golden file also lists the root causes that must **not** fire — a check that fires
everywhere detects nothing.

---

## 4. Real-Site Results

Six local sites are ones we wrote ourselves, so they cannot tell us how the audit behaves on
sites nobody designed for it. We ran it against seven public sites covering the types in the
brief: a payments platform, a government portal, a JavaScript-heavy startup, a retailer, a
personal blog, a restaurant chain and a news site. We read every finding and asked one
question of each — would a judge agree this is real?

Two results came out right without any change, and both are worth pointing at:

**The news site refuses our crawler in robots.txt.** The audit reads nothing, and the
verdict says so: *"This site's robots.txt disallows this auditor, so no pages were fetched.
That is the file working as intended; it also means any crawler that respects it sees
exactly as little."* An empty report is not presented as a result.

**The restaurant chain returns 403 to unrecognised user agents.** The finding reads "The
homepage refuses this crawler" and gives bot-manager fix steps, rather than claiming the
site is down. Those are different problems with different owners.

Site names are deliberately absent from this repository, as the brief requires.

---

## 5. False Positives We Found and Fixed

Eleven, all from the real-site run. These are the ones with the widest blast radius:

| Problem | Effect before the fix | Fix |
|---|---|---|
| URL types matched part of a word | A blog post at `/2004/Jun/29/job/` was classified as a careers page, and `/2002/Jul/3/alternativeValidatorIcons/` as a comparison page | Match whole path segments. Any dated permalink is an article regardless of its slug |
| Share buttons counted as social profiles | A `facebook.com/sharer/` link counted as the brand's Facebook page, so every site with share widgets looked well corroborated | Exclude share and intent URLs |
| Personal profiles counted as company profiles | A customer story linked to an individual's LinkedIn, which was then counted as the company's | LinkedIn must be `/company/` or `/school/` |
| Wikidata search matches by prefix | "GOV.UK" returned "GOV.UK One Login" and "GOV.UK Verify", so the audit reported an organisation colliding with its own services | Require a close label match |
| Footers listing every year | A footer reading "© 2002 2003 2004 … 2026" was read as a copyright year of 2002 | Read the whole run of years, not just the anchored one |
| Ordinal dates did not parse | "3rd November 2025" was not recognised, so pages showing clear dates were reported as undated | Accept `st`, `nd`, `rd`, `th` |
| Orphan detection on large sites | The crawler sees 20 pages of 3,000 and cannot know the other 2,980 do not link to a page | Skip the check when the sample is too small to support the conclusion |

The remaining four were narrower: modal class-name matching flagged 18 of 20 pages on a
retail site because `class="modal-opener"` contains both "modal" and "open"; interface
controls such as "Menu" were counted as navigation destinations; a one-word title produced
a 100% title-body drift score; and a single page with a menu among nineteen without one was
treated as the site's navigation standard.

Every fix is covered by the test suite, and `good-site` still reports zero.

---

## 6. Open Items

Nothing blocks submission. Three things are worth a decision:

**The Playwright branch has never executed.** No Playwright is installed on our machines,
so `--render` falls back to static analysis every time. The fallback path is tested and the
report states honestly when the pass did not run. The rendered branch itself is unverified.
Installing Playwright and auditing one site would close this.

**Whether `tests/` and `evals/` ship in the zip.** They add roughly 150 KB. We include them:
the rubric scores engineering hygiene, and `good-site` is the clearest evidence the audit
does not invent findings. Excluding them is a one-line change in `package.py`.

**Verification of our own work.** `VERIFICATION.md` splits this into four independent
workstreams that four people can run in parallel with no dependencies between them.

---

## 7. Where to Read More

| Document | Contents |
|---|---|
| `README.md` | What the marketplace does, how it is built, how to run it |
| `DECISIONS.md` | Every judgement call, including four differences between the live agentskills.io specification and the brief |
| `VERIFICATION.md` | The four-person parallel review plan |
| `evals/README.md` | Three prompts a judge is likely to type, and what a correct response looks like |
| `skills/audit-orchestrator/references/mechanism-model.md` | The seven mechanisms every check traces back to |
| `skills/audit-orchestrator/references/round2-failure-modes.md` | Each Round 2 failure mode, and the check that now detects it |

---

<div align="center">
  <sub>Adobe University Hackathon 2026 · Round 3 · Brand AI-Readiness Audit</sub>
</div>
