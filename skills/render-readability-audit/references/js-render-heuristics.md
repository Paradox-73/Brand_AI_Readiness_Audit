# JavaScript render and readability heuristics

Every threshold in `render-readability-audit`, the reasoning behind the number, and the false
positive each guard exists to prevent.

The governing rule: **a short page is not a broken page.** A thin contact page and an empty
React shell both have little text, and the difference between them is the whole job. That is
why, with the two exceptions below, no single signal is enough.

The second governing rule, which is a rule about this skill rather than about a site:
**one question, one answer.** Shell detection and thin-copy detection are the same question
asked from two sides, and while they were answered separately they could contradict each
other about one page. They did, on an education site's homepage, and the report carried both
answers — see below.

---

## Shell detection: two signals, or one of two shapes

A content page is called a JavaScript shell when **two or more** of these hold:

| Signal | Threshold | Why this number |
|---|---|---|
| Text vs script payload | under 300 chars visible text **and** over 50 KB of script | Below ~300 characters a page cannot carry a self-contained quotable fact. 50 KB of script alongside that is a page whose bytes are almost entirely instructions for assembling content that has not arrived. |
| Empty framework root | a known root selector containing under 200 chars | A mount point with nothing in it is the signature of client-side rendering. 200 rather than 0 because frameworks often inject a loading skeleton, a spinner label or an SSR comment. |
| Content in a state blob | one of the sixteen names below present, with under 300 chars of visible text in the whole delivered document | The content demonstrably exists in the response — as JSON for the framework, not as HTML for a reader. This is the most diagnostic single signal, and also the most quotable evidence. **One bar, not the doubled one this used to carry.** The double existed because the extracted body copy excludes the menu and the footer, so 300 was too tight a floor for it; the whole document's visible text includes them and needs no allowance. |
| `<noscript>` demands JavaScript | matches "enable/requires JavaScript" | The site says so itself. |

State-blob names checked, sixteen of them: `__NEXT_DATA__`, `__NUXT__`, `__NUXT_DATA__`,
`__INITIAL_STATE__`, `__APOLLO_STATE__`, `__REDUX_STATE__`, `__remixContext`,
`self.__next_f`, `__sveltekit_`, `__staticRouterHydrationData`, `__PRELOADED_STATE__`,
`__SERVER_DATA__`, `viewerModel`, `wixBiSession`, `warmupData`, `siteAssets`.

**The last six are the point.** The first ten are all names a developer picks by choosing a
framework, so a hosted site builder's payload was invisible to every one of them: a shop's
homepage carried one of those names fifteen times and 558,468 bytes of script, 42.9% of the
document, and the report said it had no framework mount point and no state blob. The list is
still a list and can still be wrong about a builder nobody here has met, so the report now
prints which names were searched rather than only that none was found.

Root selectors checked: `#root`, `#app`, `#__next`, `#__nuxt`, `#gatsby-focus-wrapper`,
`[data-reactroot]`, `[data-server-rendered]`, `[ng-app]`, `#ember-app`, `#svelte`, `#q-app`.
They match an id **exactly**, so a build that names its mount anything outside this list
records no framework root at all — which is exactly how the page below reached the thin-copy
check with one signal instead of two.

**The false positive two signals prevent.** A correctly server-rendered Next.js page has
`__NEXT_DATA__` *and* a populated root *and* full body text. It trips one signal and is
correctly left alone. Requiring two is what separates "uses a framework" from "ships an empty
container".

### Two shapes that are enough on their own

Nothing but client-side assembly produces either, so neither needs a second signal.

**An empty framework root beside almost no text.** Required because a React or Vue build whose
scripts are all external never trips the script-payload signal, and that is the commonest
shape on the web.

**Script dwarfing the whole delivered document.** Inline script (state blobs included)
outweighing the document's *entire* visible text by **50 bytes per character**, where that
entire visible text is itself under 300 characters.

The word doing the work is *entire*: navigation, header and footer included, not the extracted
body copy. A site's own copy can be thin for an ordinary reason — a contact page is three
lines — but every real page still delivers its menu and its footer as HTML, so it clears the
300-character floor on its chrome alone before the ratio is ever consulted. A container
delivers neither, because both are inside the framework too. 50 rather than a larger number
because the case being excluded is an ordinary page carrying a big analytics or consent
bundle, and that page never reaches the ratio.

**The false negative this fixes.** An education site's homepage: 227,011 bytes delivered, of
which 224,637 were inline script, twelve characters of visible text, and a mount node whose id
this list does not hold. One signal. Shell detection said "every crawled content page delivered
readable text in the initial HTML response"; `thin-html` fired at high severity in the
confident tier, said "the rest of the delivered document is accounted for by the site's own
repeated header, menu and footer, so there is no content this audit failed to find", and told
the owner to write two or three sentences of body copy. The chrome accounting behind that
sentence compares visible text against visible text and had never read the 224,637 bytes.

### Precedence: a container is never thin copy

A page carrying *any* shell signal is a container, and no later check may describe it as a page
somebody wrote too little on. The two defects have different fixes — render on the server
against write more — so getting this wrong does not soften the advice, it replaces it.

| Signals | `spa-shell-detection` | `thin-html` |
|---|---|---|
| Two, or one sufficient shape | reports `js-shell` | sets the page aside, names it, never counts it |
| Exactly one | names the page in its reason; no finding | sets the page aside, names it, never counts it |
| None | — | judges the page |

One signal alone is too little to accuse a template of shipping empty and far too much to tell
an owner the copy was never written, so that page is named by both checks and counted by
neither.

## The homepage is judged separately

An empty homepage is the worst case on any site — it is what almost every crawler and
assistant tries first — so it gets its own finding at its own severity:

| Rendered pass | Recovered text | Severity | Confidence |
|---|---|---|---|
| Ran | **≥ 300 chars** — enough to quote | high | high |
| Ran | **< 300 chars** | critical | high |
| Ran but failed on this page | unknown | critical | **medium** |
| Did not run | unknown | critical | **medium** |

The bar is `MIN_QUOTABLE_TEXT`, the same 300 characters shell detection uses, not a separate
900. Nine hundred called a homepage that went from 13 characters of static HTML to 538 after
rendering "not reachable even with JavaScript enabled" — the opposite of what had just been
measured, and a `critical` verdict on a site whose real problem is `high`.

**Two questions, kept apart.** "Is there now enough text to quote" (≥ 300 chars) decides the
severity and the sentence. "Did JavaScript supply the bulk of it" — rendered text at least
**3x** the static text — only chooses the wording: where most of the text was already in the
delivered HTML, the finding says so instead of implying the page was empty. Fused into one
boolean, a homepage the browser read 1,500 characters of was told "a Playwright pass recovered
only 1,500 chars, so the content is not reachable even with JavaScript enabled", at critical
severity, leading the report.

The last two rows are the honest ones. When the browser never ran, or ran and failed on this
one page, the audit cannot confirm whether JavaScript would recover the text, so it reports the
severity the evidence supports and lowers the *confidence* rather than hedging the severity.
The two are told apart in the finding text, because "no browser was available" sends a reader
to install one they already have.

The first row matters too: if rendering recovers the content, the fix is still needed — many
crawlers do not execute JavaScript — but the problem is narrower, so it is `high`, not
`critical`.

## Site-wide shells

More than **50%** of crawled content pages being shells is a template-level failure (`high`);
fewer is `medium`. The distinction is not about count but about cause: above half, it is one
or two templates, and fixing those fixes everything.

Unless the browser says otherwise. Five pages site-wide are rendered, chosen as a spread
across page types, so only some of the shells are among them — and if every shell that *was*
rendered came back with 300+ characters and more than 3x its static text, the
finding drops to `medium` and says so — how many were rendered, that JavaScript supplied
readable text on all of them, and that the rest are assumed to behave the same way. A shop
built on a JavaScript framework was told 30 of 33 pages ship empty, and asked for several days
of re-architecting; the pages were not empty, the browser had simply been given four and a half
seconds to hydrate them.

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

What this check is for, stated without commerce in it: **a linked file holds something the
site says nowhere else and a reader would need.** That is as true of an ordinance as of a
spec sheet. It used to have one branch, written for a manufacturer hiding a datasheet and
gated on whether the site sells anything, and the gate encoded that origin: a town whose
agendas, minutes, ordinances, emergency management plan and primary ballot are every one of
them PDF-only got the sentence "nothing on this site is for sale, so a linked PDF is not
hiding a price or a specification a buyer needs" and nothing else. The check was switched
off precisely where PDFs carry the most content.

Two branches now, asking the same question of two different kinds of document.

**Prices — for a site that sells something.** Both conditions required:

1. A link whose text matches: price, pricing, rate card, tariff, fee, cost, quote, spec,
   specification, datasheet, brochure, catalogue, menu, product sheet, technical detail,
   dimensions, sizing, price list.
2. **No price appears anywhere in the page's own text.**

The second condition is the guard: a pricing page that states its prices *and* offers the
PDF has locked nothing. The commerce gate stays on this branch and has to. Its only escape
hatch is "the page states a price", which a charity, a museum, a government department and a
documentation site can never reach, so without the gate each of them would be told
permanently that its pricing facts are locked inside a brochure.

**Documents of record — for every site.** Both conditions required:

1. A link whose text names a kind of document rather than a subject: agenda, minutes,
   ordinance, bylaw, resolution, warrant, ballot, annual report, audit report, budget,
   financial statement, public or legal or hearing notice, proclamation, zoning map or
   regulations, a named plan (town, city, village, management, master, comprehensive,
   emergency, strategic, action, development), application form, permit application, code of
   ordinances. Narrow on purpose: `report` alone matches half the anchors on the web,
   `annual report` names one publication; `plan` alone matches "plan your visit".
2. **Under 300 characters of the page's own words, once the link labels are subtracted.**

The subtraction is the part that matters. A register is a page whose text *is* its list of
links — forty lines reading "Minutes, 14 March (PDF)" — so counting `body_text_len` there
counts the labels and concludes the page explains itself. What is left after subtracting them
is what the page says *about* the documents, which is all a reader who cannot open them has.
`pdf_links` is capped at 20 entries, so on a longer register the subtraction is short and the
number comes out too high; that direction makes the check quieter, never louder.

This branch's escape hatch is reachable by anybody: **say in HTML what the document says.**
A minutes page carrying three paragraphs of what was decided is a page an assistant can
quote whatever the signed PDF beneath it holds.

**What may be claimed.** This audit fetches a PDF's headers when it checks the link. It does
not parse the file. So the finding says *this page does not state what the document states* —
a claim about the HTML, which is supportable — and never anything about what is inside the
document. The evidence line says so in as many words.

**Both branches** drop a PDF that reads as somebody else's document: a citation in the
sentence around the link, or a reference-list heading above it. A site cannot republish what
it does not own.

**The wording follows the site kind**, through `site_kind`. A public body is told to publish
each decision as HTML and keep the PDF as the signed copy of record; every other site is told
to restate the document and keep the PDF as the download. Saying something different to a
town than to a manufacturer is the point; saying nothing to the town was the defect.

### Video — a transcript check, not a video check

An embedded or native video **that could be carrying something**, **and** no `<track>`
caption element, **and** no transcript keyword nearby, **and** under 800 chars of body text.

The 800-char condition does one half of the work. A page that explains itself in prose
alongside a video is not relying on the video, and flagging it would penalise good practice.

The first condition does the other half, and it was missing. A `<video>` carrying
`autoplay`, `muted` and `loop` **together** is a silent background loop: wallpaper, the
modern replacement for an animated GIF, sitting behind a hero heading. It has no beginning
and no end and it says nothing, so there is nothing in it to transcribe. A dental clinic was
told that 12 of its 21 pages "lead with audio or video and carry little readable text" and
advised to "generate a transcript (most video platforms produce one automatically) and paste
it into the page" and to "add a 3 to 5 sentence written summary above the player stating what
the video says" — about one clip in its site-wide template.

**All three attributes, never one or two.** `muted` alone is ordinary on real content video
that starts silent and unmutes on a click; `autoplay` alone says only that the site is
impolite. `loop` is the one nothing with a running time carries: a viewer who reaches the end
of an explainer, an interview or a demo is not returned to its start. Requiring the
conjunction is what keeps this rule from deleting the finding on the pages it was written for.

Read from the two counts the crawl already records: `autoplay_count` is every
`<video autoplay>`, `intrusive_autoplay_count` is those not also `muted` and `loop`, and the
difference is the elements carrying all three. A third-party embed exposes no attributes to a
static read, so an embed and an `<audio>` element always count as players that could be
carrying something. A page whose *only* players are background loops is set aside and named.

**Two claims were welded together, and only one survives.** "Leads with video" and "carries
little readable text" were one finding, and the first was false on every page it fired on
here. The second is a real defect, it is `thin-html`'s to report, and it is judged there on
its own 300-character bar with the fix that belongs to it — write a paragraph, not transcribe
a clip that says nothing. The video check's reason names the set-aside pages and says how
many of them are short, so neither claim disappears silently.

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

Only images **nothing describes at all**, on pages with 5+ images, at `low` severity.

An explicit `alt=""` is the *correct* markup for a decorative image and is deliberately not
counted. Conflating the two is the most common error in automated accessibility reporting,
and it would mean telling a site that got it right that it got it wrong.

Four more descriptions count as a description and are also not counted as missing: an
`aria-label`, an `aria-labelledby`, a `title`, `role="presentation"`, and a `<figcaption>`
inside the image's own `<figure>`. Anybody working through this by hand against the `alt`
attribute alone will over-count against what the check does.
