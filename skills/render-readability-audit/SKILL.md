---
name: render-readability-audit
description: Check whether a machine can actually read the HTML a site delivers. Detects JavaScript shells where the real content only exists after hydration, pages whose substance is locked inside images, PDFs, video or iframes, and catalogues hidden behind a load-more button with no crawlable pagination. Compares static HTML against a Playwright-rendered DOM, whenever a browser is available, to measure exactly how much text JavaScript supplies. Use when a site looks complete in a browser but is missing or thin in AI answers and search results, when a brand runs a React, Next.js, Vue or Angular site, or as the second gate of a full AI-readiness audit.
license: MIT
compatibility: Requires Python 3.10+ with beautifulsoup4 and lxml. Makes no network requests itself. Playwright is optional; when it is installed the crawl runs a rendered pass by default and this skill measures the JavaScript gap instead of inferring it.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "A C"
  gate: "can read"
---

# Render and readability audit (gates A/C: can a machine read it?)

## When to use

Run after `crawl-access-audit`. This is the gate where a page that looks finished to a
person turns out to be empty to a machine: the crawler was let in, fetched the URL, and
received a container with no content in it.

Reach for it on its own when a site is built on a client-side framework, when someone says
"the page is right there, I can see it", or when search and AI results show a brand's pages
as thin or title-only.

Pages are counted as documents, not as addresses. A URL and the same path on the site's
other hostname — `www` and the bare domain — answering with the same title and the same
amount of body text are one document, whether or not a canonical says so. Counting both
inflates every denominator this skill prints. That the site answers on two hostnames
with no canonical is itself a finding, and `crawl-access-audit` raises it.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- The snapshot carries `rendered_text_len` per page whenever the crawl was able to run a
  browser. That pass runs by default when Playwright is installed, so on most machines
  the field is present; `--no-render` suppresses it and an absent Playwright means the
  crawl records why in its notes.
- A page the crawl re-read from the rendered DOM carries `static_view`, holding the lengths,
  the script counts and the shell markers of the document the server actually sent. This
  skill reads that copy and no other, because every finding here is about the difference
  between what arrived and what a browser produced. The other five skills read the rendered
  record, which is the page a visitor sees.

## Procedure

Twelve checks are registered; they are grouped here into eight reading steps, in this order.
`references/render-checks.md` lists every registered check id with the exact text field it
reads, its thresholds and where each number came from, where it declines, and the severity it
can reach.

**Which text field a check reads is the whole of its meaning here.** The whole delivered
document's visible text — menu and footer included — decides whether a page is a container.
The extracted body copy, with that furniture removed, decides whether somebody wrote too
little. A storefront measured 173 on the second and 3,517 on the first, and reading the wrong
one produced that report's critical finding, its high finding and its first two Start-here
items, all false.

1. **Decide once whether each content page is a container.** Four signals: almost no visible
   text alongside a lot of script, an empty framework root, the real content sitting in a
   state blob, a `<noscript>` telling the visitor to enable JavaScript. Two independent
   signals make a `js-shell` finding, and two of them are sufficient alone. **This answer is
   computed here and read by every later check**, because when two checks asked separately
   they disagreed about one homepage and the report led with the wrong fix.
2. **Judge the homepage separately.** An empty homepage is the worst case on any site.
   **Critical** where a browser read under 300 characters or no browser ran, **high** where
   it read 300 or more — the content exists, but only for consumers that execute JavaScript.
3. **Then judge the rest, and say how much of it you actually saw.** More than half the
   content pages being shells is a template failure (**high**); fewer is **medium**. At most
   five were ever opened in a browser, so name how many, and drop a step where every one of
   them recovered its text.
4. **Measure the render gap where you can.** Rendered against static text on up to five
   pages spread across page types. A gap of 60% or more is the content that disappears for
   any consumer that does not run JavaScript. Playwright's absence is a property of this
   machine and is never reported as a site problem.
5. **Separate a page that is empty from a page this audit read badly** (check 4b in the
   reference). Measure the site's own header-and-footer furniture per template, then say
   which of four conclusions each short page reached: empty, badly read, undecided, or set
   aside as a container. Only the first is a `thin-html` finding. The bar is 300 characters
   of English and is restated in the page's own script: a Han character carries about three
   English characters of meaning, so the same paragraph in Chinese is judged against 100.
6. **Read the visible text for the template's own source.** A moustache or block
   expression, a merge field between sentinels, or a shortcode standing alone: the page is
   publishing an instruction to its own template instead of the content that instruction was
   supposed to produce, and an assistant quoting the page quotes the placeholder. Seven
   punctuation shapes, no word list, so the reading is the same in any language. Position and
   proportion decide severity — **high** where the placeholder is the page's whole content or
   sits in its title, `<h1>` or meta description, **medium** where it repeats across the
   template, is on the homepage, or a page carries two of them, **low** for one stray field.
   A page whose whole content is a placeholder is set aside by the thin-text check above with
   the placeholder named, because "write two or three sentences" is the wrong instruction for
   copy that exists in a template that did not run.
7. **Find facts locked in non-text.** Images on a page with almost no text; a pricing PDF on
   a site that sells, or a document of record on any site, with nothing equivalent in the
   page's own words; a video with no caption track or transcript; a non-video iframe on a
   page with nothing of its own. Skip anything already reported as a shell.
8. **Check pagination, then alt text.** "Load more" with no numbered pagination hides a
   catalogue from a crawler that does not press buttons. Images described by nothing at all
   are the lowest finding here — never an explicit `alt=""`, which is correct, and never an
   image named by an `aria-label`, a `title`, a `role="presentation"` or its own
   `<figcaption>`.

Two of the twelve are not in the list above because neither reads the site.
`render-mode` records, in the report's own appendix, whether a browser was used, so every
claim below is stamped with which of the two readings it rests on; it can never produce a
finding. `client-rendered-chrome` is the case the shell test misses: body copy that arrived
while the navigation, footer and heading did not. It is **high** at half the content pages,
and it exists because four unrelated findings on one storefront — no navigation, no H1, no
contact route, no off-site profiles — were one root cause.

Three rules govern all twelve, and are what to carry over when working through this by hand.

- **One question, one place.** Whether a page is a container is decided in check 1 and
  nowhere else. A shell is fixed by rendering on the server and thin copy by writing more;
  two answers to one question means one of the two fixes is wrong.
- **A claim about pages is capped by the pages you opened.** Say how many were rendered and
  that the rest are an assumption. A shop was told to spend "several days of development
  time" re-architecting rendering of pages that render correctly.
- **State only what was measured.** This audit reads a PDF's headers and never its text, so
  the claim is that *this page does not state what the document states* — about the HTML —
  and never a claim about what is inside the file.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), `signals{shell_page_count, render_mode}`.

Findings carry mechanism `C` (or `A` for uncrawlable pagination) and a `root_cause` of
`js-shell`, `thin-html`, `unrendered-template-artefact`, `image-locked-facts`,
`pdf-locked-facts`, `no-transcript`, `iframe-content`, `uncrawlable-pagination` or
`alt-missing`.

A finding here that says something is **missing** carries `checked[]`, the independent
sources it consulted and found empty. One source caps it at medium confidence and the report
says so. Working through this skill by hand, do the same: say where you looked, and look in a
second place before stating at high confidence that something is not there. Every false
positive this marketplace has made on a real site was one detector with a finite list of
shapes, reported as a fact about the site.

## Not applicable when

- No content page returned HTTP 200 — there is no delivered HTML to assess.
- Playwright is not installed: the render-gap check is skipped and recorded as an audit
  environment limitation, explicitly not as a site defect.
- A page is already reported as a shell: it is excluded from the image, iframe and PDF
  checks so one underlying problem produces one finding.
- A page links to a pricing PDF *and* states a price in its own text, or links a document of
  record *and* says in its own words what the document says. Nothing being for sale is a
  reason not to run the price half; it is not a reason to say nothing about the documents.
- A linked PDF is somebody else's document, cited rather than published here. A site cannot
  republish what it does not own.
- A video page carries 800+ characters of body text, or its only player is a
  `<video autoplay muted loop>` — a silent background loop with no speech to transcribe.
- No category or listing pages were crawled: pagination cannot be assessed.
- A template artefact the site itself prints inside a code sample — `code_text`, which the
  crawl records separately from the prose — is never counted as a defect, on any page. Where
  more than half the pages carry a code sample the site publishes code for a living, and only
  a placeholder standing as a page's whole content is read at all.
- A short page carrying any shell signal, or one this audit probably failed to extract, or
  one it could not judge either way. Each is named in the reason rather than dropped, and the
  passing sentence counts only the pages actually assessed.

`references/render-checks.md` gives the wording each of these reasons has to reach, and the
report that made each one necessary.

## References

- `references/render-checks.md` — every one of the twelve registered check ids: the text
  field it reads, its thresholds and where each number came from, where it declines, the
  severity it can reach, and the real report each guard was written against.
- `references/js-render-heuristics.md` — every threshold, the reasoning behind the number,
  and the false positives each one exists to prevent.
