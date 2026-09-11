# How the crawl works, and what it will not do

`SKILL.md` gives the command and the caps. This file gives the reasoning: why each cap is the
number it is, what the crawl records so that a later skill does not have to spend a request
rediscovering it, and the exact boundary of the read-only promise.

Everything here is about `scripts/crawl.py`, which runs once. The six sub-skills read its
snapshot and — apart from a small, declared request budget each — fetch nothing themselves.

---

## The budget, and why it is 115 seconds rather than 240

`scripts/crawl.py <origin> --out snapshot.json --budget 115`.

240 is the script's own default and is wrong for a composed run: step 3's six skills are owed a
share of the same five minutes. The six sub-skills' wall clock is reserved **before** the crawl
starts, not after — the crawl is told 115 s and killed at 125, the boundary of a 148 s reserve
it cannot reach.

A skill given a network budget is killed a whole wedged response (40 s) after the second it was
told to stop, not two, so spending its allowance can no longer end it. `crawl.py` checkpoints a
readable snapshot every five pages, so cutting the crawl costs pages instead of the run.

Measured on a site answering in six seconds a page: 29 pages / 5 skills / 1 of 3 probing /
273.5 s became 21 pages / 6 skills / 3 of 3 / 231.7 s. A fast site still reaches the sixty-page
ceiling.

---

## What it fetches, in order

robots.txt, the sitemaps, `/llms.txt`, the homepage, up to 8 sitemap URLs sampled with seed 42
and spread across detected page types, and then breadth-first from the homepage to depth 2.

Hard caps: 60 pages, the wall clock it is given, 10 s per request, 30 s total per request, 5 MB
read per response, 0.5 s between requests, single threaded.

**The two per-response caps exist because `requests`' own timeout counts silence between bytes
rather than elapsed time.** Measured: one slow server held a single fetch open past 260 s, and
one large asset had 125 MB read into memory before its content type was looked at. A response
stopped at the size cap is recorded as truncated and the report says so.

---

## Only pages are fetched, decided in two steps

An ending the crawl recognises as never a page — every compression suffix, every package format,
patches, signatures, checksums, media, documents — costs no request at all.

Every other URL costs one request, and its body is read only if the content type says it is a
page. A HEAD would cost that same request and then need a second one for the body of a real
page, so HEAD is never the cheaper test.

**The ending list can only save a request and correctness never rests on it**, which is what
stops the next unknown suffix reopening the hole: a static site publishing its releases as
`.tar.bz2` had eight of them and a `.patch` downloaded in full, 17.8 MB, because `gz` and `tar`
were listed and `bz2` was not.

A response that was never a page spends no page slot — those nine took eight of sixteen — and
leaves every count of how much of the site was read.

---

## The rendered pass, and what it is allowed to conclude

When Playwright is installed the crawl also renders a spread of five pages, and any page whose
delivered HTML holds almost no text is then re-read from the browser's document, so the other
five skills judge the page a visitor sees rather than an empty container.

If that happens to two or more of the five, the site's whole delivery model is client-side and
the pass keeps going through the remaining thin pages, emptiest first, until its budget runs out.

The delivered length is kept as `static_text_len`, because the JavaScript finding is the gap
between the two numbers and is still true of the site.

**A thin page the browser never reached carries `render_skipped`. No check may assert a
page-level absence about one**: the delivered stub and the document a visitor sees are different
pages, and on a client-rendered site a heading, a description and a viewport tag all arrive with
the JavaScript.

---

## Measurements made once, so no later check has to pay for them

**HEAD honesty.** One HEAD request to the homepage establishes whether this site answers HEAD the
same way it answers GET. Three of eight major commercial sites do not, and the three checks that
verify links read that one measurement rather than each rediscovering it.

**Sitemap shape.** `fetch_sitemaps` records `content_type`, `body_bytes` and `looks_like_html` on
each sitemap record, because telling a sitemap from a homepage otherwise costs a request.

**`/llms.txt`.** The presence test reads the content type first and sniffs 4000 characters, not
400.

---

## The two stopping conditions

- **The homepage does not respond after two attempts.** Stop and report one critical finding: an
  unreachable homepage makes every other check meaningless.
- **robots.txt disallows this auditor at `/`.** No pages are fetched at all. Report the access
  findings from robots.txt alone and mark everything else not applicable.

---

## The read-only promise, in full

**GET and HEAD only.** No forms submitted, no cookies sent that the site did not set, nothing
fetched under `/cart`, `/checkout`, `/login`, `/account` or `/admin`, and nothing robots.txt
disallows.

**A page that *redirects* to one of those is dropped as well.** A school's `/calendar` answers 302
to `/login`, and grading where it landed put a finding about a sign-in screen in front of an owner
who cannot edit it.

The auditor identifies itself as `BrandAIReadinessAudit/1.0 (+read-only audit)`.

### The one documented exception to the user-agent rule

The AI-user-agent comparison probe in `crawl-access-audit`. It is skipped when robots.txt
disallows the agent, tries two agents from different operators, and re-asks once to confirm a
differing status — three requests at most.

### The two third-party services, both in `freshness-corroboration-audit`

**Profile links.** Each is a page fetch, so that host's own robots.txt is read first and a platform
that disallows automated requests is reported as unchecked rather than probed. LinkedIn and X both
disallow every client at `/`, and probing them anyway was a hole in the promise above.

**Wikidata.** Its robots.txt is read first, like every other host's. The search the name check
needs lives under `/w/api.php`, and Wikidata's robots.txt disallows `/w/` to every user agent,
so no lookup is made and the name-collision check is reported as unanswered rather than as a
pass. A lookup is made only on a run where that file allows it, and then the auditor identifies
itself by name as Wikimedia's user-agent policy requires.

---

## Doing this without Python

Every sub-skill's `SKILL.md` names its checks and points at the reference file carrying them in
full. Fetch the same pages by hand (GET only, respecting robots.txt), work through each skill's
procedure, and record findings in the shape described in `references/report-schema.json`.

You will reach the same findings; the scripts are the executable form of the same logic, not a
different method.
