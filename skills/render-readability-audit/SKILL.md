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

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- The snapshot carries `rendered_text_len` per page whenever the crawl was able to run a
  browser. That pass runs by default when Playwright is installed, so on most machines
  the field is present; `--no-render` suppresses it and an absent Playwright means the
  crawl records why in its notes.

## Procedure

1. **Decide whether each content page is a shell.** Require **two independent signals**
   before saying so, because a genuinely short page is not a broken one:
   - under 300 characters of visible text alongside more than 50 KB of script
     (300 because below it a page carries no quotable fact);
   - a framework root (`#root`, `#app`, `#__next`, `#__nuxt`, `[data-reactroot]` and
     similar) containing under 200 characters;
   - the real content sitting in `__NEXT_DATA__`, `__NUXT__`, `__INITIAL_STATE__`,
     `__APOLLO_STATE__` or `self.__next_f` rather than in HTML;
   - a `<noscript>` block telling the visitor to enable JavaScript.

2. **Judge the homepage separately.** An empty homepage is the worst case on any site, so
   it gets its own finding. If a rendered pass ran and recovered the text, report **high**
   and say the content exists but only for consumers that execute JavaScript. If a rendered
   pass ran and recovered nothing, report **critical** with high confidence. If no rendered
   pass was available, report **critical** with *medium* confidence and say why.

3. **Then judge the rest.** More than half the crawled content pages being shells is a
   site-wide template failure (high); fewer is medium. Name the pages, not the count alone.

4. **Measure the render gap where you can.** With Playwright, compare rendered text length
   against static text length on up to 5 sampled pages. The five are spread across page
   types rather than taken in crawl order: the crawl is breadth-first, so the first five are
   the homepage and the top-nav pages, which on a hybrid site are exactly the ones rendered
   on the server, while the shells this check exists to find live on product and article
   pages one level down. A gap of 60% or more is the exact
   amount of content that disappears for any consumer that does not run JavaScript.
   **Playwright's absence is a property of the auditing machine. Never report it as a site
   problem** — record it in `checks_run` and move on.

5. **Find facts locked in non-text.** Skip anything already reported as a shell so nothing
   is counted twice.
   - **Images**: a page under 400 characters of body text carrying a large or hero image, or
     five or more images, or 15+ SVG text nodes, or a `<canvas>`.
   - **PDFs**: a link whose text or filename promises pricing, a rate card, a spec sheet, a
     datasheet, a brochure, a menu or dimensions — *and* no equivalent figure anywhere in the
     page's own text. If the price is on the page too, the PDF is a convenience, not a lock.
   - **Video**: an embedded or native video with no caption track, no nearby transcript, and
     under 800 characters of body text. A page that explains itself in prose alongside a
     video is fine.
   - **Iframes**: a non-video, non-map iframe on a page with under 300 characters of its own
     text. An iframe is a separate document; the parent reads as empty.

6. **Check pagination on listing pages.** A category page containing "load more" or "show
   more" with no numbered pagination links in the HTML hides its catalogue: a crawler does
   not press buttons, so everything past the first batch has no URL to follow.

7. **Alt-text hygiene, last and lowest.** Count only images with **no `alt` attribute at
   all**, on pages carrying five or more images. An explicit `alt=""` on a decorative image
   is correct and must not be counted.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (always 0), `signals{shell_page_count, render_mode}`.

Findings carry mechanism `C` (or `A` for uncrawlable pagination) and a `root_cause` of
`js-shell`, `thin-html`, `image-locked-facts`, `pdf-locked-facts`, `no-transcript`,
`iframe-content`, `uncrawlable-pagination` or `alt-missing`.

## Not applicable when

- No content page returned HTTP 200 — there is no delivered HTML to assess.
- Playwright is not installed: the render-gap check is skipped and recorded as an audit
  environment limitation, explicitly not as a site defect.
- A page is already reported as a shell: it is excluded from the image, iframe and PDF
  checks so one underlying problem produces one finding.
- A page links to a pricing PDF *and* states a price in its own text: the facts are not
  locked, so nothing is reported.
- A video page carries 800+ characters of body text: the page explains itself regardless of
  the video.
- No category or listing pages were crawled: pagination cannot be assessed.

## References

- `references/js-render-heuristics.md` — every signal, its threshold, the reasoning behind
  the number, and the false positives each guard exists to prevent.
