# The twelve render and readability checks, one section each

`SKILL.md` names the questions in reading order. This file holds the measurement: every
signal, the guard around it, and the false positive or false negative each guard was written
against. `references/js-render-heuristics.md` is the companion — it explains why a delivered
document and a rendered document differ, and where each number came from.

The order matters. The shell decision is made once, and every later check reads that one
answer rather than asking again.

---

## The register: twelve check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report, so a reader can tick this list off against a real run. `SKILL.md` groups them into
seven reading steps; this is the ungrouped set.

**Which text field a check reads is the whole of its meaning here, and getting it wrong cost
this skill its worst finding.** Two different quantities are involved and they are not
interchangeable:

- **the whole delivered document's visible text** — `spa_shell.visible_text_len`, falling back
  to `text_len`. Menu, header and footer included. This is what the shell decision reads,
  because a container ships no menu either.
- **the extracted body copy** — `body_text_len`. Site furniture removed. This is what the
  "did the author write enough" checks read.

A server-rendered storefront measured **173 against 3,517** on those two fields. The shell
test was reading the second, produced that report's critical finding, its high finding, its
top-sheet line and its first two Start-here items, all false, all prescribing several days of
development for work already done. Four more checks in this file said "almost no text, so the
content must be elsewhere" off the same wrong field. Each entry below states which field it
reads.

---

### `render-mode` — did this audit read the site with a browser, or only the HTML the server sent?

- **Measures:** `crawl.render_mode` on the snapshot. Nothing about the site.
- **Threshold:** none.
- **Declines:** always. This id is registered and never answered with a finding, on purpose,
  so that the report states in the appendix which of the two readings every check below rests
  on. Rendered: *"this audit read the site with a browser, so the JavaScript gap below is
  measured rather than inferred."* Not rendered: *"this audit read only the HTML the server
  delivered; where that matters, the checks below say so and lower their own confidence."*
- **Ceiling:** none. It cannot produce a finding.

### `spa-shell-detection` — is this page a container the content never arrived in?

- **Measures:** the **whole delivered document**. `_delivered_text_len` reads
  `spa_shell.visible_text_len`, then `text_len`, then `body_text_len` — in that order, so the
  furniture-stripped field is the last resort and never the first. Alongside it:
  `scripts.inline_bytes` and `spa_shell.state_blob_bytes` (combined with `max`, not a sum),
  `spa_shell.root_selector` and `root_text_len`, `spa_shell.state_blobs`,
  `spa_shell.noscript_demands_js`, and `scripts.external_count`.
- **Thresholds.** `MIN_QUOTABLE_TEXT = 300` — below 300 characters a page holds no
  self-contained fact worth quoting. `HEAVY_SCRIPT_BYTES = 50000` — 50 KB of script beside no
  prose means the page is assembled in the browser. `EMPTY_ROOT_TEXT = 200` — a framework
  mount point holding under 200 characters has not rendered. `SCRIPT_BYTES_PER_CHARACTER = 50`
  — chosen against the whole document rather than the body copy, because every real page ships
  its menu and footer as HTML and a container ships neither; 50 rather than a larger number
  because what is being excluded is an ordinary page carrying an analytics or consent bundle.
  `EXTERNAL_BUNDLE_COUNT = 3` — a code-split build ships no inline script at all.
  `SHELL_SHARE_HIGH = 0.5` — more than half the content pages being shells is the template
  rather than a page. All measured or reasoned as stated; none is a round number picked to
  look tidy.
- **Declines.** Three separate refusals, and they are what this check is for. A page whose
  extraction plainly failed — under a quarter of the document kept, `extraction_looks_incomplete`
  in `audit_common.py` — with no state blob and no framework root is **not judged at all**: a
  page we failed to read is not a page with nothing on it. A page delivering **300 or more
  visible characters** returns `unsettled` however many shell signals it carries, never
  `shell`; a mount point sitting empty while 300 characters arrive around it is worth
  recording and is not enough to accuse the template of shipping nothing. And a page carrying
  exactly one weak signal under 300 characters is also `unsettled` — too little to accuse the
  template, far too much to accuse the copy — so it is named in both this check's reason and
  `thin-html`'s, and counted in neither. Where no content page returned 200:
  *"no content pages returned HTTP 200, so there is no delivered HTML to assess."*
- **Ceiling:** **critical**, on the homepage only, and only where the browser read under 300
  characters or no browser ran (confidence `high` in the first case, `medium` in the second,
  and the finding says which). Every other page tops out at **high**, at over half the content
  pages; **medium** below that, and **medium** at medium confidence where every rendered
  sample recovered its text. `mechanism="C"`, `root_cause="js-shell"`.

### `static-vs-rendered-text-gap` — how much of the text does a consumer that runs no JavaScript lose?

- **Measures:** `rendered_text_len` (the browser's `document.body.innerText`) against
  `_static_text_len`, which reads `static_text_len`, then `text_len`, then `body_text_len`.
  **Whole document on both sides.** Comparing the browser's whole document against the static
  *body* manufactured a gap on a page that had none: 461 against 2,270 where the static
  document held 2,643.
- **Thresholds.** `RENDER_GAP_SIGNIFICANT = 0.6` — JavaScript adding 60% or more of the final
  text means the delivered HTML is a stub. `MIN_QUOTABLE_TEXT = 300` — a gap is only counted
  where the rendered page exceeds it, so two short numbers cannot produce a large ratio.
- **Declines:** wherever no rendered pass ran, and the reason names **which** of four causes
  applies rather than reaching for the stock one — no page returned 200, the crawl spent its
  wall clock, Playwright's own note from the snapshot, or the pass simply not running. Each is
  suffixed *"This is a property of the audit run, not a defect on the site."* Telling somebody
  no browser was available on a run where one was sends them to install what they already have.
- **Ceiling:** **high** at high confidence, one tier only. `mechanism="C"`,
  `root_cause="js-shell"`.

### `client-rendered-chrome` — is the layout itself waiting on JavaScript, on a page whose copy arrived?

- **Measures:** a framework marker present (`spa_shell.root_selector` or
  `spa_shell.state_blobs`) while **all four** of `links.nav`, `links.footer`,
  `chrome_signature.nav_path_count` and the H1 count (the larger of `headings_in_markup.h1`
  and `headings.h1`) are zero. Pages the crawl re-read from the browser DOM are excluded: what
  the browser produced is not evidence about what the server delivered.
- **Threshold:** `CHROME_IN_BROWSER_SHARE = 0.5`, the same half as `SHELL_SHARE_HIGH` and for
  the same reason — below it this is one odd page, above it it is the template every page is
  built from.
- **Declines:** where no content page's delivered HTML was available; where every page ships
  its own navigation, footer or heading; and **below the half share**, where the reason names
  the pages and says plainly that this is under the level at which it would be the template.
- **Ceiling:** **high** at high confidence. `mechanism="C"`, `root_cause="js-shell"`.

This check exists because four unrelated findings on one client-rendered storefront — no
navigation, no H1, no contact route, no off-site profiles — were one root cause, and
`spa-shell-detection` passed the page because its body copy was there.

### `thin-html` — did the author write too little, as distinct from the page being a container?

- **Measures:** `body_text_len`, the **extracted body copy** — this check's subject really is
  what somebody wrote. Cross-checked against `_furniture_size`: the median of
  `text_len - body_text_len` per chrome signature, which is the header, menu and footer this
  template strips.
- **Thresholds.** `MIN_QUOTABLE_TEXT = 300`, **in English characters**, which is what it was
  measured in. `quotable_floor(page)` restates it in the characters the page is actually
  written in, through `audit_common.CHARACTERS_PER_ENGLISH_CHARACTER`: Han 3.0, kana 2.2,
  hangul 1.8, everything else 1.0. So the bar is 300 on a Latin, Cyrillic, Greek, Arabic,
  Hebrew, Devanagari or Thai page and **100 on a Han-script one**. Those three ratios are
  **chosen from published translation-length ratios, not measured here**, and each is
  deliberately nearer 1.0 than the ratio it stands for, because the direction that costs more
  is calling a real page substantial. "People's Republic of China" is 26 characters and seven
  in Han; before this, a page holding one paragraph of Chinese was told it carries too little
  text to be quoted. The finding prints the bar it applied, not the constant.
  `CHROME_VARIATION_CHARS = 300` and `CHROME_VARIATION_SHARE = 0.25` — the allowance is the
  furniture plus whichever of those two is larger. `CHROME_SAMPLE_MIN_PAGES = 3` — with two
  pages the median is an average of two and one outlier moves it. `THIN_SHARE = 0.3` — a
  third of content pages too short to quote is a site-wide problem.
- **Declines:** four separate set-asides, each named in the reason rather than dropped —
  pages reported as shells, pages carrying one shell signal, pages this audit probably failed
  to extract, and pages no template baseline could judge either way. The passing sentence
  counts only the pages actually assessed, because *"each of the 0 pages this check could
  assess carries at least 300 characters"* is what a one-page crawl of a shell printed.
- **Ceiling:** **high** at a third or more of content pages, **medium** below; confidence
  `high`. `mechanism="C"`, `root_cause="thin-html"`.

### `unrendered-template-artefact` — is the page publishing its own template source?

- **Measures:** the page record **as it stands** — `text`, the whole visible document, and
  `body_text` for the proportion. This is the one reading in this skill that does *not* use
  `static_view`: on the single-page app that produced the defect the expression is visible
  after the framework has run, and what a reader is shown is the subject. Also `title`,
  `headings.h1` and `meta_description`, read separately because a `<title>` is not visible
  text and is the slot a broken template fills most often. `code_text` and
  `code_sample_count` are the guard.
- **Thresholds.** Seven punctuation shapes in `TEMPLATE_ARTEFACT_PATTERNS`, no word list:
  `{{ … }}`, `{% … %}`, `%%FIELD%%`, `[[field]]`, `${field}`, `#field_name#` (underscored or
  three-plus upper case, so `#sale#` is not one), and a shortcode — one tag word, optional
  `key=value` attributes, so `[read more]` is not one. `ARTEFACT_DOMINANT_SHARE = 0.1` with
  the remainder under `MIN_QUOTABLE_TEXT = 300`: **measured**, not chosen. The real
  store-locator page rebuilt through this marketplace's own extractor records
  `body_text` as *"Store locator Store locator [wpsl]"* — 6 artefact characters in 34, 17.6% —
  because the extracted copy repeats the page's `<h1>`, so a fifth would have declined the
  page the defect was found on. `ARTEFACT_TEMPLATE_PAGES = 3`, the same number as
  `CHROME_SAMPLE_MIN_PAGES` and for the same reason. `CODE_PUBLISHING_SHARE = 0.5` — above
  half the pages carrying a code sample, the site publishes code for a living.
- **Refuses to claim:** that a string the site itself prints inside a `<pre>` or a standalone
  `<code>` is a defect — anywhere on the site, matched on the exact string with every space
  removed, because `visible_text` deletes the space after an opening brace and `code_text`
  keeps it, so a naive comparison matches nothing and the guard is silently off. On a page
  that displays code at all, or a site where most pages do, only the "this is the page's whole
  content" reading is kept. The shortcode shape is never read in the middle of prose.
- **Declines:** where no page prints one; the reason states how many pages were read, that the
  match was on shapes rather than words, and how many pages were held back for carrying code.
- **Ceiling:** **high** where the placeholder is the page's whole content or sits in the title,
  the `<h1>` or the meta description, at high confidence; **medium** where the same string
  repeats across three or more pages, where the homepage carries one, or where one page
  carries two different ones; **low** at medium confidence for a single stray field, which is
  the observation being certain and its being a defect being the weaker half.
  `mechanism="C"`, `root_cause="js-shell"` — the shared vocabulary has no name for this
  defect and adding one takes `ROOT_CAUSES` and `EVIDENCE_BASIS` in `audit_common.py`; the
  title, the evidence and the fix say which of the three shapes was found, so nothing a reader
  sees rests on the tag.

The same defect showed up on three unrelated sites: `{{getCtrlKey()}}` and `#native_company#`
served as visible text by a single-page app that got **0 findings** here, and a
store-locator page whose entire content is `[wpsl]`, reported as *"1 page carries too little
text to be quoted"* — true, and never naming the plugin, which was the entire fix. This check
runs over **every page that answered 200**, not only the ones this audit could type: the claim
is about a literal string in the document, so the page type cannot make it false.

### `facts-locked-in-images` — is the page's message in pixels rather than characters?

- **Measures:** `body_text_len` (**extracted body copy**) beside `images.large_image_count`,
  `images.svg_text_nodes`, `images.canvas_count`, and an image count with the per-template
  median subtracted, so a drawer menu's icons on every page are not this page's pictures.
- **Thresholds:** `IMAGE_DOMINANT_TEXT = 400`, deliberately above `MIN_QUOTABLE_TEXT` because
  dominance needs a higher bar than thinness. Then one large image, or five images of its own,
  or 15 SVG text nodes, or one `<canvas>`.
- **Declines:** shells; fully described image sets; and any page whose text this audit
  probably failed to keep. **It never claims to have read the image.** There is no pixel
  reading anywhere in this skill: the evidence is a text length, an image count and the
  presence or absence of a description, and the finding says so.
- **Ceiling:** **medium** at medium confidence, one tier. `mechanism="C"`,
  `root_cause="image-locked-facts"`.

### `facts-locked-in-pdfs` — is a fact only in a download?

- **Measures:** `pdf_links` (the anchor's text and URL) against the page's own text. Two
  independent halves: a *price* half gated on the site selling something, and a *documents of
  record* half asked of every site.
- **Thresholds:** `PDF_INDEX_PROSE_FLOOR = 300` characters of the page's own words **once the
  PDF link labels are subtracted**, because a register whose text is its list of links
  otherwise measures as a page with plenty on it. `PDF_INDEX_SHARE_HIGH = 0.3`, the same share
  as `THIN_SHARE`. `CITATION_CONTEXT_CHARS = 300` — long enough to hold the author list and
  year in front of an anchor, short enough that the next paragraph is not what decides it. The
  two word lists are deliberately narrow: `report` on its own matches half the anchors on the
  web.
- **Declines:** a page that states the price in its own text; a page that says in its own
  words what the document says; a PDF that reads as somebody else's, cited rather than
  published here. **It never opens the file.** The claim is *this page does not state what the
  document states* — a claim about the HTML — and the finding says that in its own evidence.
- **Ceiling:** **high**, and only on the documents-of-record half at 30% or more of pages;
  the price half is **medium**. Confidence stays `medium` however many pages match, because
  this audit read the anchor and the page and never the file. `root_cause="pdf-locked-facts"`.

### `video-transcripts` — is something said aloud and written nowhere?

- **Measures:** the `video` record — `native_count`, `embed_count`, `audio_count`,
  `track_count`, `transcript_nearby`, and the autoplay/controls counts that identify a
  background loop — beside `body_text_len` (**extracted body copy**).
- **Thresholds:** `VIDEO_EXPLAINS_ITSELF_TEXT = 800`, higher than `MIN_QUOTABLE_TEXT` because
  one quotable paragraph is not a substitute for forty minutes of speech.
- **Declines:** a page whose only player carries `autoplay`, `muted` and `loop` **together**
  is wallpaper and is set aside by name; a caption track or a nearby transcript; 800 or more
  characters of prose; and a page this audit failed to extract. Where a wallpaper page is also
  short, the reason says that is `thin-html`'s question on its own bar and not attributed to
  the video here.
- **Ceiling:** **medium** at medium confidence. `mechanism="C"`, `root_cause="no-transcript"`.

### `iframed-main-content` — is the content a separate document the parent does not contain?

- **Measures:** the `iframes` list with video, map and invisible frames removed, beside
  `body_text_len` (**extracted body copy**).
- **Threshold:** `MIN_QUOTABLE_TEXT = 300`.
- **Declines:** shells, and pages this audit failed to extract — an absence claim needs a
  reliable text reading under it.
- **Ceiling:** **medium** at medium confidence. `mechanism="C"`, `root_cause="iframe-content"`.

### `image-alt-coverage` — are there images nothing describes at all?

- **Measures:** `images.undescribed_count`, falling back to `images.missing_alt_count`, on
  pages carrying five or more images.
- **Threshold:** five images on the page. There is no named constant for it.
- **Declines:** an explicit `alt=""` is correct on a decorative image and is not counted —
  and **neither are four other descriptions the earlier version of this file did not mention**:
  an `aria-label`, an `aria-labelledby`, a `title`, `role="presentation"`, or a `<figcaption>`
  inside the image's own `<figure>`. Anybody working through this by hand off the old wording
  would over-count against what the check does.
- **Ceiling:** **low** at high confidence — the lowest thing this skill reports, on purpose.
  `mechanism="C"`, `root_cause="alt-missing"`.

### `crawlable-pagination` — is the catalogue past the first batch reachable at all?

- **Measures:** pages the crawl typed `category`. The body text is searched for a load-more
  phrase, and `links.internal` for anything matching a pagination shape (`?page=`, `/page/N`,
  `?offset=`, `/all/` and a dozen more).
- **Thresholds:** two phrase lists, no numbers.
- **Declines:** no category pages were crawled; the load-more phrase is the label of an actual
  link rather than a button; real pagination links exist; and — the important one — where the
  link list was **truncated at 300** before pagination links could have been seen, the check
  cannot judge and says so rather than reporting an absence from a list it knows is short.
- **Ceiling:** **medium** at medium confidence. This is the only check in this skill carrying
  `mechanism="A"` rather than `C`: it is a crawlability failure whose cause is rendering.
  `root_cause="uncrawlable-pagination"`.

---

## The reasoning behind each, in reading order

The seven steps below are the order the checks run in and the order `SKILL.md` states the
questions in. They carry the reasoning; the register above carries the register.

## 1. Decide once whether each content page is a container

Ask it here and nowhere else. Two checks in this skill asked it separately and disagreed about
one homepage: 227,011 bytes delivered, 224,637 of them inline script, twelve characters of
visible text. Shell detection wanted two signals, found one, and reported the site clean; the
thin-copy check fired at high severity in the confident tier and told the owner to write a
paragraph. A shell is fixed by rendering on the server and thin copy by writing more, so the
report led with the wrong fix.

The signals:

- under 300 characters of visible text alongside more than 50 KB of script (300 because below
  it a page carries no quotable fact);
- a framework root (`#root`, `#app`, `#__next`, `#__nuxt`, `[data-reactroot]` and similar)
  containing under 200 characters;
- the real content sitting in one of **sixteen** state-blob names rather than in HTML —
  `__NEXT_DATA__`, `__NUXT__` and `self.__next_f` among them, and six more added because the
  first ten are all names a developer picks by choosing a framework, so a hosted site
  builder's payload was invisible to every one of them. The full list is in
  `references/js-render-heuristics.md`;
- a `<noscript>` block telling the visitor to enable JavaScript.

**Two independent signals**, because a genuinely short page is not a broken one — with two
exceptions, each sufficient on its own because nothing but client-side assembly produces it:

- **An empty framework root on a page with almost no text.** Required because a build whose
  scripts are all external never trips the script signal, which is the commonest shape on the
  web.
- **Inline script outweighing the whole delivered document's visible text** — navigation,
  header and footer included — by 50 bytes per character, where that whole visible text is
  itself under 300 characters. Measured against the whole document and not against the
  extracted body copy: a thin contact page still ships its menu and its footer as HTML, and a
  container ships neither, because both are inside the framework too. This one needs no mount
  node, which is what let the homepage above through — the root selectors match an id exactly,
  so a build naming its mount anything outside the list records no root at all.

**Precedence.** A page carrying *any* of these signals is a container, and no later step may
describe it as a page somebody wrote too little on. Two signals or a sufficient one is a
`js-shell` finding; one signal alone is too little to accuse a template and far too much to
accuse the copy, so name that page in both checks' reasons and count it in neither.

---

## 2. Judge the homepage separately

An empty homepage is the worst case on any site, so it gets its own finding.

If a rendered pass ran and the browser read **300 characters or more** — the same bar as shell
detection, enough text to quote — report **high** and say the content exists but only for
consumers that execute JavaScript. If it read fewer than 300, report **critical** with high
confidence.

Keep two questions apart. How much text there is now decides the severity and the sentence.
Whether JavaScript supplied *the bulk* of it — rendered text at least three times the static
text — only chooses the wording, so a page whose text was mostly in the delivered HTML already
is told so instead of being described as empty. Fused into one boolean, a homepage the browser
read 1,500 characters of was told "a Playwright pass recovered only 1,500 chars, so the content
is not reachable even with JavaScript enabled", at critical severity, leading the report.

If the browser ran but failed on this one page, or no rendered pass was available at all, report
**critical** with *medium* confidence and say which of the two it was — telling someone no
browser was available, on a run where one was, sends them to install what they already have.

---

## 3. Then judge the rest, and say how much of it you actually saw

More than half the crawled content pages being shells is a site-wide template failure (**high**);
fewer is **medium**. Name the pages, not the count alone.

This finding is a claim about every page in its list, and at most five of them were ever opened
in a browser. Say how many were rendered, what those showed, and that the rest are an assumption.

If every rendered shell recovered its text — 300+ characters and more than three times its static
text — drop to **medium** with medium confidence: the evidence you have points the other way. A
shop was told 30 of its 33 pages ship empty and to spend "several days of development time"
re-architecting rendering, and its pages render correctly — the browser had been given four and a
half seconds to hydrate them.

---

## 4. Measure the render gap where you can

With Playwright, compare rendered text length against static text length on up to 5 sampled
pages. The five are spread across page types rather than taken in crawl order: the crawl is
breadth-first, so the first five are the homepage and the top-nav pages, which on a hybrid site
are exactly the ones rendered on the server, while the shells this check exists to find live on
product and article pages one level down. A gap of 60% or more is the exact amount of content
that disappears for any consumer that does not run JavaScript.

Wait for the rendered text to stop growing rather than for a fixed pause. A commerce site built
on a JavaScript framework needed about eleven seconds to hydrate; measured after half a second it
looked empty, and was reported as empty.

**Playwright's absence is a property of the auditing machine. Never report it as a site
problem** — record it in `checks_run` and move on. Say which reason applies rather than reaching
for the stock one: a report once explained that Playwright was unavailable on a run whose own
startup line said Playwright had been found, when the truth was that the crawl was blocked before
there was anything to render.

---

## 4b. Separate a page that is empty from a page this audit read badly

Start from check 1's answer: a page carrying any shell signal is set aside here by name, never
judged.

Then a short page is only a finding when the text is genuinely absent, and the ratio between the
extracted body text and the whole document cannot tell that from a page read badly — on a page
that is all chrome and no content that ratio is always tiny, so the guard written to stop false
positives guaranteed a false negative on exactly the pages this check exists to find. A report
passed `thin-html` with "each of the 56 pages this check assessed carries at least 300 characters
of body text" on a site one of whose 56 carries 59.

**Measure the site's own furniture instead.** On every page whose extraction plainly worked —
anything with 300+ characters of body text — the difference between the whole document's visible
text and the extracted body text is the header, menu and footer that were stripped out. Take the
median of that per chrome signature, so each template is measured against its own pages, falling
back to the site-wide median where a template has fewer than three such pages.

Then for each short page:

- if the text outside its extracted body is no more than that furniture plus a tolerance (300
  characters, or a quarter of the furniture, whichever is larger), there was never any content to
  lose and **the page really is empty**;
- if it holds substantially more, that surplus is **text this audit failed to keep** and the page
  is not reported;
- where no page of any template extracted well, neither answer is available: report those pages as
  **undecided**, by name, in the not-applicable reason.

Say which of the four the check concluded — empty, badly read, undecided, or set aside as a
container — every time. A page dropped silently is a page the reader believes was counted, and a
passing sentence counting pages it never assessed is the same defect: "each of the 0 pages this
check could assess carries at least 300 characters" is what a one-page crawl of a shell printed.

The evidence line states only what was measured. The chrome accounting compares visible text
against visible text and never reads the script bytes, so it cannot support "there is no content
this audit failed to find" — a claim about the whole delivered document, printed at high severity
in the confident tier about a document that was 99% script.

A fifth answer joins those four, and it comes from `unrendered-template-artefact` rather than
from any measurement here: **the page's whole content is a placeholder**. A store-locator page
whose copy is `[wpsl]` is short for a reason this check cannot see, and the real report on it
said "1 page carries too little text to be quoted" and never named the shortcode — true, useless,
and the name of the broken plugin was the entire fix. Such a page is set aside with the string
quoted in the reason, and the reason says plainly that writing more copy is not the fix.

---

## 4c. Read the visible text for the template's own source

The defect is one sentence: **the page is publishing an instruction to its own template instead
of the content that instruction was supposed to produce.** An assistant quoting the page quotes
the placeholder, and unlike almost everything else this audit reports, the owner can usually fix
it in minutes once they know — which is what makes the miss expensive.

Three things separate this from a false-positive machine, and all three are in the register entry
above.

- **Documentation legitimately prints these.** A templating tutorial, an API reference and this
  repository's own README all contain `{{ value | json }}`. The crawl already separates a sample
  from prose — `code_text` holds every block `<pre>` and standalone `<code>`, `body_text` is the
  page with exactly those removed — so the rule is the site's own: a string it prints as a sample
  anywhere is never accused anywhere. Compare with every space removed, or the guard matches
  nothing.
- **Position and proportion.** `[wpsl]` as a page's entire content is a broken plugin; the same
  string inside a paragraph explaining shortcodes is documentation. So the loosest shape is read
  only where the artefact *is* the page, and the naming slots — title, `<h1>`, meta description —
  are read only on a page that shows no code at all.
- **Say which artefact and where.** The evidence quotes the exact string beside the URL and the
  slot it stood in, so a reader can check it in View Source in seconds; the shortcode tag is the
  handle the plugin registers, which is what the owner searches for.

---

## 5. Find facts locked in non-text

Skip anything already reported as a shell so nothing is counted twice.

**Images.** A page under 400 characters of body text carrying a large or hero image, or five or
more images, or 15+ SVG text nodes, or a `<canvas>`.

**PDFs, two questions, and only the first is about money.**

*Prices*: a link whose text promises pricing, a rate card, a spec sheet, a datasheet, a brochure,
a menu or dimensions, on a site that sells something, with no equivalent figure anywhere in the
page's own text. Keep the commerce gate on this half: a museum has no price anywhere, so telling
it its pricing facts are locked in its brochure has no way to be right.

*Documents of record*: a link whose text names an agenda, minutes, an ordinance, a bylaw, a
budget, a ballot, an annual report, a public notice or a named plan, on a page holding under 300
characters of its own words **once the link labels are subtracted**. Ask this one of every site. A
town whose agendas, minutes, ordinances, emergency plan and ballot are all PDF-only was declined
with "nothing on this site is for sale", so the check switched off exactly where PDFs carry the
most content. Subtract the labels because a register whose text *is* its list of links measures as
a page with plenty on it.

Say what you read: this audit fetches a PDF's headers, never its text, so the claim is *this page
does not state what the document states* — a claim about the HTML — and never a claim about what is
inside the file. Word the fix for the site: a town is told to publish the decision as HTML and keep
the PDF as the signed copy of record; anyone else is told to restate the document and keep the PDF
as the download.

**Video, and only where there is something to transcribe.** An embedded or native video with no
caption track, no nearby transcript, and under 800 characters of body text. A page that explains
itself in prose alongside a video is fine.

A `<video>` carrying `autoplay`, `muted` and `loop` **together** is a silent background loop —
wallpaper, the replacement for an animated GIF — and holds no statement to write down: set that page
aside and name it. Require all three. `muted` alone is ordinary on content video that starts silent
and unmutes on a click, and `autoplay` alone says only that the site is impolite; `loop` is what no
explainer, interview or demo carries, because a viewer who reaches the end of something with a
running time is not sent back to its start. A third-party embed exposes no attributes to a static
read, so it is never assumed decorative.

Two claims used to be welded into one finding here — "leads with video" and "carries little readable
text" — and a clinic was told to transcribe a silent template clip on 12 of its 21 pages. Only the
second claim survives, and it belongs to `thin-html` on its own 300-character bar, with the fix that
goes with it.

**Iframes.** A non-video, non-map iframe on a page with under 300 characters of its own text. An
iframe is a separate document; the parent reads as empty.

---

## 6. Check pagination on listing pages

A category page containing "load more" or "show more" with no numbered pagination links in the HTML
hides its catalogue: a crawler does not press buttons, so everything past the first batch has no URL
to follow.

---

## 7. Alt-text hygiene, last and lowest

Count only images **nothing describes at all**, on pages carrying five or more images. An
explicit `alt=""` on a decorative image is correct and must not be counted — and neither is an
image named by an `aria-label`, an `aria-labelledby`, a `title`, `role="presentation"` or the
`<figcaption>` inside its own `<figure>`. Five carve-outs, not one.

---

## Why the not-applicable reasons are worded as they are

- **Playwright is not installed.** Recorded as an audit environment limitation, explicitly not as a
  site defect.
- **A page is already reported as a shell.** Excluded from the image, iframe and PDF checks so one
  underlying problem produces one finding.
- **A page links to a pricing PDF *and* states a price in its own text**, or links a document of
  record *and* says in its own words what the document says: the facts are not locked, so nothing is
  reported. Nothing being for sale is a reason not to run the price half; it is not a reason to say
  nothing about the documents.
- **A linked PDF is somebody else's document**, cited rather than published here — a reference list,
  or a citation in the sentence around the link. A site cannot republish what it does not own.
- **A page whose only player is a `<video autoplay muted loop>`.** There is no speech in it, so no
  transcript is missing. Name that page in the reason, and say that whether it is also too thin is
  `thin-html`'s question and not this one's.
- **A short page carrying any shell signal.** It is a container, reported or named as one by check 1,
  and `thin-html` never judges it. "Write two or three sentences of body copy" is the wrong
  instruction for a page whose copy arrives when the script runs.
- **A short page this audit probably failed to extract, or could not judge either way.** Both are
  named in the reason rather than dropped: a page that was set aside is not a page carrying 300
  characters, and the passing sentence counts only the pages actually assessed.
