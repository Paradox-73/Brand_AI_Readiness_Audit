# JavaScript render and readability heuristics

Every threshold in `render-readability-audit`, the reasoning behind the number, and the false
positive each guard exists to prevent.

The governing rule: **a short page is not a broken page.** A thin contact page and an empty
React shell both have little text, and the difference between them is the whole job. That is
why no single signal is ever enough.

---

## Shell detection: two signals required

A content page is called a JavaScript shell only when **two or more** of these hold:

| Signal | Threshold | Why this number |
|---|---|---|
| Text vs script payload | under 300 chars visible text **and** over 50 KB of script | Below ~300 characters a page cannot carry a self-contained quotable fact. 50 KB of script alongside that is a page whose bytes are almost entirely instructions for assembling content that has not arrived. |
| Empty framework root | a known root selector containing under 200 chars | A mount point with nothing in it is the signature of client-side rendering. 200 rather than 0 because frameworks often inject a loading skeleton, a spinner label or an SSR comment. |
| Content in a state blob | `__NEXT_DATA__`, `__NUXT__`, `__INITIAL_STATE__`, `__APOLLO_STATE__`, `__REDUX_STATE__`, `__remixContext`, `self.__next_f` present, with under 600 chars of text | The content demonstrably exists in the response — as JSON for the framework, not as HTML for a reader. This is the most diagnostic single signal, and also the most quotable evidence. |
| `<noscript>` demands JavaScript | matches "enable/requires JavaScript" | The site says so itself. |

Root selectors checked: `#root`, `#app`, `#__next`, `#__nuxt`, `#gatsby-focus-wrapper`,
`[data-reactroot]`, `[data-server-rendered]`, `[ng-app]`, `#ember-app`, `#svelte`, `#q-app`.

**The false positive this prevents.** A correctly server-rendered Next.js page has
`__NEXT_DATA__` *and* a populated root *and* full body text. It trips one signal and is
correctly left alone. Requiring two is what separates "uses a framework" from "ships an empty
container".

## The homepage is judged separately

An empty homepage is the worst case on any site — it is what almost every crawler and
assistant tries first — so it gets its own finding at its own severity:

| Rendered pass | Recovered text | Severity | Confidence |
|---|---|---|---|
| Ran | ≥ 900 chars | high | high |
| Ran | < 900 chars | critical | high |
| Did not run | unknown | critical | **medium** |

The third row is the honest one. Without Playwright the audit cannot confirm whether
JavaScript would recover the text, so it reports the severity the evidence supports and
lowers the *confidence* rather than hedging the severity. The finding text says so explicitly.

The first row matters too: if rendering recovers the content, the fix is still needed — many
crawlers do not execute JavaScript — but the problem is narrower, so it is `high`, not
`critical`.

## Site-wide shells

More than **50%** of crawled content pages being shells is a template-level failure (`high`);
fewer is `medium`. The distinction is not about count but about cause: above half, it is one
or two templates, and fixing those fixes everything.

## Render gap

With Playwright, up to 5 pages are compared:

```
gap = (rendered_text_len - static_text_len) / rendered_text_len
```

A gap of **60% or more**, on a page whose rendered text exceeds 300 chars, is reported. The
gap is not a proxy for anything — it is literally the proportion of the page that does not
exist for a consumer that does not run JavaScript.

**Playwright's absence is never a finding.** It is a property of the auditing machine.
It is recorded in `checks_run` and stated in the report appendix.

---

## Facts locked in non-text

Pages already reported as shells are excluded from all of these, so one underlying problem
produces one finding.

### Images — under 400 chars of body text, plus imagery

Triggered by a large image (width × height ≥ 120,000 px, or a class/id/filename containing
`hero`, `banner`, `masthead`, `cover`, `splash`), or 5+ images, or 15+ SVG text nodes, or a
`<canvas>`.

400 rather than 300 because this check is about *dominance*: a page with a hero image and a
couple of real paragraphs is fine, and 400 chars is roughly where the text stops being
incidental to the picture.

Reported at `medium` with `medium` confidence — the audit cannot read the image to confirm it
carries facts, and says so.

### PDFs — the fact must be missing from the page

Two conditions, both required:

1. A link whose text or filename matches: price, pricing, rate card, tariff, fee, cost,
   quote, spec, specification, datasheet, brochure, catalogue, menu, product sheet,
   technical detail, dimensions, sizing, price list.
2. **No price appears anywhere in the page's own text.**

The second condition is the guard. A pricing page that states its prices *and* offers a PDF
download has locked nothing — the PDF is a convenience. Only a page that points at a PDF
*instead of* stating the numbers is reported.

### Video — a transcript check, not a video check

An embedded or native video, **and** no `<track>` caption element, **and** no transcript
keyword nearby, **and** under 800 chars of body text.

The 800-char condition is doing the work. A page that explains itself in prose alongside a
video is not relying on the video, and flagging it would penalise good practice. Only a page
whose substance is spoken aloud is reported.

### Iframes — excluding video and maps

A non-video, non-map iframe on a page with under 300 chars of its own text. An iframe is a
separate document; a consumer reading the parent sees a frame element, not the content
inside it. Video and map embeds are excluded because they are normal and are covered
elsewhere.

---

## Crawlable pagination

A category page containing "load more", "show more", "view more" or "load additional", with
no numbered pagination link (`?page=`, `/page/2`, `?offset=`, `?start=`) anywhere in its
HTML.

A crawler does not press buttons. Everything past the first batch has no URL to follow, so
most of the catalogue is never fetched. Restricted to `category` pages: a "show more" on an
FAQ accordion is a different thing entirely and is not reported.

---

## Alt text — counting absence, not emptiness

Only images with **no `alt` attribute at all**, on pages with 5+ images, at `low` severity.

An explicit `alt=""` is the *correct* markup for a decorative image and is deliberately not
counted. Conflating the two is the most common error in automated accessibility reporting,
and it would mean telling a site that got it right that it got it wrong.
