# Progress

This document gives the status of the Round 3 submission. We update it as work lands.

It follows Simplified Technical English (ASD-STE100): short sentences, active voice, and
one idea per sentence.

## What we built

A marketplace of seven skills. It audits a website for two problems:

- AI assistants cannot find the brand, or they describe it incorrectly.
- Visitors arrive from an AI answer, and then they leave.

One skill is the entrypoint. It crawls the site one time. Six other skills read that one
crawl. The entrypoint then merges their findings into one report.

## Status

The work is complete. All 169 tests pass in about one minute. Two validators pass on all
seven skills: the official `skills-ref` tool, and our own `validate_marketplace.py`.

| Part | Status |
|---|---|
| 7 skills, each with a SKILL.md | Done |
| 12 reference documents | Done |
| Crawler, 6 checks, report composer | Done |
| 6 local test sites | Done |
| Test suite | Done. 169 tests pass |
| README, DECISIONS, evals | Done |
| Real-site check | Done. 7 sites |
| Package check | Done. The zip is 262 KB |

## How the audit runs

The crawl budget is fixed. The crawler reads a maximum of 30 pages. It waits 0.5 seconds
between requests. It stops after 240 seconds. It uses only the GET and HEAD methods.

The crawler obeys these safety rules:

- It does not read `/cart`, `/checkout`, `/login` or `/admin`.
- It obeys robots.txt. If robots.txt refuses the crawler, the crawler stops.
- It does not send forms. It does not change the site.

Each of the six sub-skills examines one gate:

| Skill | Question it answers |
|---|---|
| `crawl-access-audit` | Can the crawler get in? |
| `render-readability-audit` | Can the crawler read the page? |
| `structured-data-audit` | Can a machine read the facts? |
| `fact-extractability-audit` | Is there a sentence to quote? |
| `freshness-corroboration-audit` | Is the brand current, and do other sources agree? |
| `engagement-audit` | Does the visitor stay? |

The report sorts the fixes by priority. Priority is severity, multiplied by reach, divided
by effort. Severity alone is not sufficient. A robots.txt fix takes one minute and it
unblocks the whole site. That fix comes before a rewrite that corrects one page.

## How we test the work

We built six local websites. Each one contains a different fault. A small test server
sends these sites over HTTP. The server can also refuse a named crawler, and it can set
response headers.

One site is the most important. `good-site` has no faults. It must produce zero findings.
We found seven bugs when we made this site produce zero findings. Each bug would also
occur on a normal, well-built site. The worst bug compared prices as text. The audit
therefore reported `480.00` in the markup and `$480` on the page as a contradiction.

The golden files record the root causes that must occur. They do not record exact text.
The test server uses a random port number, and exact text would contain that number.

## What we found on real sites

We audited seven public websites. They cover the types in the brief: a payments platform,
a government portal, a JavaScript startup, a retailer, a personal blog, a restaurant chain
and a news site. We read every finding, and we asked one question. Would a judge agree
that this finding is correct?

We found and corrected 11 incorrect findings. These are the important ones:

- The audit matched URL types on part of a word. A blog post at `/2004/Jun/29/job/`
  therefore became a careers page. The audit now matches complete parts of the path. Any
  page with a date in its address is an article.
- The audit counted share buttons as social profiles. A `facebook.com/sharer/` link is not
  a Facebook page. This error made every site look better connected than it is.
- The audit counted a personal LinkedIn profile as the company profile. A customer story
  linked to that person.
- Wikidata search matches the start of a name. A search for "GOV.UK" therefore returned
  "GOV.UK One Login" and "GOV.UK Verify". The audit reported a name conflict with the
  organisation's own services.
- A footer listed each year from 2002. The audit read the copyright year as 2002.
- The audit could not read the date "3rd November 2025". It therefore reported pages with
  clear dates as pages with no date.
- The audit reported orphan pages on large sites. The crawler reads 20 pages of 3,000, so
  it cannot know that the other pages do not link to them. The audit now omits this check
  when the sample is too small.

Two results were correct, and we did not change them:

- The news site refuses our crawler in robots.txt. The audit stops, and the report explains
  why. It does not present an empty audit as a result.
- The restaurant site answers 403. The report says "The homepage refuses this crawler". It
  gives fix steps for a bot manager. It does not say that the site is down.

## Limits of the audit

Tell a customer these limits. They are decisions, and they are not faults:

- The crawler reads 30 pages. On a large site this is a sample. The report gives the number
  of pages that it read.
- Playwright is optional. Without it, the audit calculates the JavaScript gap. It does not
  measure the gap. The finding then has medium confidence.
- The audit does not sign in. It cannot see pages behind a login.
- The audit reads only the brand's own site. It cannot see what directories say about the
  brand. That is the other half of the staleness problem.
- The audit reads the markup. It does not read analytics. It finds the reasons that a
  visitor leaves. It cannot tell you that a visitor left.

## More information

`DECISIONS.md` records the decisions that we made. It includes four differences between
the live agentskills.io specification and the brief. It also explains mechanism G. We added
mechanism G because the brief describes only machine behaviour. It does not describe what
happens after a person opens the page.
