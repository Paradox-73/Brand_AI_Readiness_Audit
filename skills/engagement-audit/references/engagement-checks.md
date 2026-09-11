# The nineteen engagement checks, one section each

`SKILL.md` groups them into thirteen questions in reading order. This file holds the rest:
the exact measurement, every exemption, and the false positive each exemption was written
against. `references/engagement-heuristics.md` is the companion — it explains why a visitor
arriving from an AI answer needs something an ordinary visitor does not, and where each
number came from.

Every example below is a real defect found in a real report. They are kept because
a threshold with no failure behind it is a number somebody will "simplify" back out.

---

## The register: nineteen check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report, so a reader can tick this list off against a real run. Every finding here carries
`mechanism="G"`.

| Check id | The question | What it reads | Threshold, and where it came from | Ceiling |
|---|---|---|---|---|
| `homepage-orientation` | Does the front door say what this is and offer something to do? | the homepage's H1, its call-to-action offset in body copy with the chrome removed, its non-search forms | `CTA_BYTE_WINDOW = 1500` characters, roughly the first screen — a next step below it is not orientation. `GENERIC_H1`, a closed 14-word list, so a short brand name used as an H1 passes. A 12-word floor on the prose-orientation escape is chosen, not measured | **high**, only where there is no call to action anywhere and the site is English; **low** where the opening screen explains the brand in prose |
| `primary-navigation` | Are there at least three top-level destinations, and can a text-only reader see them? | `links.nav`, `nav_hidden_count`, `nav_visible_count`, and the footer as a fallback | `NAV_MIN = 3` — under three leaves a visitor nowhere to go. **Deliberately no upper bound**: a 29-item megamenu is normal retail and costs a machine nothing. `VAGUE_NAV_SHARE = 0.5` — one generic label among six good ones is not a defect | **high** — for a hidden menu, and for a menu under three items with no footer either |
| `homepage-links-into-the-site` | How many pages of this site can a machine reach from the homepage alone? | every `<a href>` in the delivered homepage, as addresses on this site once the page's own address, the fragment-only destinations and the other hosts are removed; then `chrome_signature.nav_path_count` as a second, wider reading | fires only at **zero**. `FRONT_DOOR_MIN_SITE_PAGES = 2` is chosen, not measured — a one-page site has nowhere onward to link, and the count is taken over the crawl and the sitemap together so a page the crawl never reached still stops it. Declines outright where the wider reading holds destinations the anchor loop did not, because that disagreement is about this audit rather than about the site | **high**, `root_cause="unreachable-from-homepage"` — its own tag rather than `dead-end`, because a crawler discovering nothing at the front door is a different defect from a visitor having nowhere to go next, and the rule that holds an absence claim back on a site most of which was not read is keyed on the cause |
| `dead-end-pages` | Is there any way onward at all from this page? | main-content internal link count, call to action in main, non-search forms, add-to-cart controls, off-site next steps | `MIN_MAIN_LINKS = 1` — a cul-de-sac is a page with no way onward at all, not a page with few | **medium** |
| `onward-links-specific-to-the-page` | Is there a next step belonging to *this* page rather than the menu? | every internal link on a deep page minus the links four fifths of pages carry | `CHROME_CORE_SHARE = 0.8`; `FURNITURE_MIN_PAGES = 8` — on a five-page crawl four fifths is four pages, so one section menu would become site furniture and every page would read as carrying nothing of its own | **medium**, under `dead-end` rather than a new root cause |
| `breadcrumb-navigation` | Does a deep page show a visible trail? | the trail read three independent ways — a breadcrumb-named container, an `itemListElement` chain, and the **addresses**: links whose paths are strict prefixes of this page's own | `BREADCRUMB_TRAIL_MAX_ITEMS = 8`, `BREADCRUMB_TRAIL_MAX_CHARS = 300`, `BREADCRUMB_TRAIL_MIN_LINKS = 2`, `BREADCRUMB_ANCESTOR_SHARE = 0.5` — a trail is almost entirely ancestors, a menu is not; `TRAIL_CONTAINER_LOOKUP = 4`, which is `<a>` in `<li>` in `<ol>` in `<nav>` | **medium** |
| `consistent-site-chrome` | Do pages carry the navigation the rest of the site carries? | `chrome_signature.nav_paths`, grouped per language edition first | `CHROME_MENU_MIN = 3` — a page carrying one link is not carrying a menu. `CHROME_CORE_SHARE = 0.8` — a link on four fifths of pages is furniture, a link on half is a section menu, and treating a section menu as the site norm is how a docs sidebar became the standard every marketing page failed. `CHROME_CORE_PRESENT = 0.5` — a checkout keeping the logo and the help link is reachable | **medium**, `root_cause="inconsistent-chrome"` |
| `orphan-pages` | Are there sitemap addresses nothing links to? | the sitemap records against every crawled page's internal, nav and footer links | five sitemap URLs minimum; and the check declines outright where the sitemap lists more than three times what the crawl reached, because an absent link then reflects the budget rather than the site | **medium** |
| `broken-internal-links` | Do this site's own links lead anywhere? | statuses the crawl already collected, then HEAD probes, each dead verdict re-asked with a GET | `BROKEN_LINK_SAMPLE = 40` — measured: a site carries a median of 292 internal targets the crawl does not fetch, so 20 was a 7% sample and the reported rate moved in five-point steps; 40 halves the step for about ten seconds of a budget with 140 to spare. `BROKEN_LINK_HIGH = 0.2` — a fifth broken is a maintenance failure rather than an accident, and this one is reasoned rather than measured | **high** over 20%, **medium** below |
| `title-body-alignment` | Does the page deliver what its title promised? | the title's content words, brand suffix removed, matched on a six-character stem against the body and the headings | under a third of the title's own words found. A page needs 400+ characters of body text and three comparable pages before anything is judged | **medium** |
| `page-headings-name-their-own-page` | Does the top-level heading change with the page, or name the site on all of them? | every page's H1 set, from both the visible document and the markup, compared whole-string | `SITEWIDE_H1_SHARE = 0.8` and `SITEWIDE_H1_MIN_PAGES = 4` — on three pages "the same on all of them" is a coincidence and the finding would rest on two comparisons | **medium** |
| `too-many-top-level-headings` | Does the page name one subject, or fifteen? | the h1 elements from both `headings` and `headings_in_markup`, folded to distinct strings | `H1_FLOOD = 10` — **chosen, not measured**: the six fixture sites carry at most one h1 on each of their 42 pages, so no corpus here can separate 10 from 3. It is chosen against the shapes either side of it — below sit sectioned articles, a screen-reader-only duplicate and a repeating slider; above sits a template using `<h1>` as a card style, which is the case seen in the wild at 15. Counting distinct strings is what makes the two readings and the slider count once | **medium** |
| `landing-page-answers` | Beside the price, does the page state stock, delivery and returns? | each fact read three ways: commerce markup, the page's own link paths, and — only on an English site — its own words | `LANDING_PAGE_MINIMUM = 3` — below three comparable pages there is no template to describe. `LANDING_FAIL_SHARE = 0.5` — telling an owner their product template is wrong on the evidence of one page is how a check earns a reputation for guessing | **medium** |
| `landing-page-leads-with-the-price` | Is the price in the first screen? | the offset of the first currency figure in the page's own body copy | `CTA_BYTE_WINDOW = 1500`, the same first screen. A character count needs no language and document order is the reading order on a phone — which is what makes this measurable and also its limit, and the finding says so | **medium** |
| `intrusive-interstitials` | Does the page cover its own content on arrival? | `interstitial.blocking_overlay` in the **delivered** markup, and nothing else | none. A dialog a page merely *contains* is never counted: component libraries ship their dialogs closed and the markup says which | **medium** |
| `form-friction` | Is an enquiry form longer than a visitor will finish? | distinct forms, folded by shape and by the template that repeats them, never `<form>` elements | `FORM_FIELD_LIMIT = 8` — measured: fires on 2 of 346 real forms, so reachable and rare. `ENQUIRY_FORM_MIN_FIELDS = 2` — one field is a subscription or a lookup, not a question | **low** |
| `page-weight` | Is the payload pathological? | `html_bytes` (bytes, not characters, so a non-Latin script is not undercounted) and `scripts.third_party_host_count` | `PAGE_WEIGHT_BYTES = 2_000_000` — measured across 302 pages on 13 real sites: median 37,510, 99th percentile 926,112, heaviest 1,258,775. The old 3 MB was 2.4× anything observed and could not fire. `THIRD_PARTY_SCRIPT_LIMIT = 15` — same corpus: median 1 host, 99th percentile 3, worst 10. The old 25 was unreachable | **medium** |
| `readability` | Can the page be skimmed? | walls of text that survive the furniture strip, and words per subheading recomputed on the post-strip word count | `WALL_OF_TEXT_WORDS = 300` — measured: fires on 21 of 123 long pages, 17%. `LONG_PAGE_WORDS = 700` — below it a page needs no internal subheadings | **low** |
| `mobile-viewport` | Is there a viewport meta tag in the delivered head? | `has_viewport` | none | **medium** where every page lacks it, **low** where some do |

Two things the table cannot show, and both matter more than any number in it.

**The money reader is one character, and it was wrong.** `landing-page-leads-with-the-price`
and every other price reading go through `find_prices` in `audit_common.py`. Its
`_MONEY_NUMBER` pattern has a `+` on the grouping run, not a `*`, and that one character was
a real defect: with `*` the first alternative matched one to three digits and stopped, so a
shop's own `₹ 1349` was read as `134` and `₹ 1199` as `119`, producing two high-severity
findings that the markup contradicted the page on pages where the two agree exactly. The
currency signs themselves are not a hand-written list — they are every character in Unicode
general category `Sc`, which is what a currency sign *is* — joined by the everyday
abbreviations those markets print (`Rs.`, `Rp`, `RM`, `RMB`), read case-sensitively and only
in front of the figure, because "12 rm" is twelve rooms.

**Three checks read English word lists, and only one of them says so.**
`landing-page-answers` gates its prose reading on the language and sets the affected pages
aside by name. `homepage-orientation` gates both its heading-wording test and its
call-to-action test, and reports only whether a heading exists. But
`primary-navigation`'s control-label and catch-all-label lists are English, and
`dead-end-pages` reads a call to action partly through `CTA_VERBS`, an English imperative
verb list — and neither check declines, caveats or lowers its confidence on a non-English
site. What limits the damage is that both have language-neutral routes to the same answer:
navigation is counted from `<nav>` links whatever they say, and a dead end is settled by the
main-content link count, the form count, the off-site link and the add-to-cart control, which
is matched on the identifiers a developer types (`add-to-cart`, `/cart/add`,
`single_add_to_cart`) rather than on what the shop's copy says. The honest statement is that
`primary-navigation` cannot report a catch-all menu on a non-English site and
`dead-end-pages`'s call-to-action signal degrades there, and neither says so in the report.

---

## 1. Homepage orientation

An H1 that is present and not one of the headings that say nothing ("Welcome", "Home",
"Hello", "Untitled" and the like — a fixed list, so a short brand name used as an H1 passes);
and a call to action within the first 1,500 characters of body text — roughly the first
screen. A CTA 4,000 characters down is not orientation.

No CTA anywhere — no link and no button — is **high**; a missing H1 alongside some other
problem, or a late or vague CTA, is **medium**.

**A missing H1 on its own is not this skill's finding.** When the heading is the only thing
wrong the check goes quiet and says so: `fact-extractability-audit` reports the heading once,
and reporting it twice made one `<h1>` into two findings, two effort estimates, one fix.

**And a missing H1 is a markup fact, not proof that a visitor is lost.** If the opening screen
carries a sentence of the shape "X is a …" in twelve words or more, the visitor is oriented
whatever the heading level, and the finding drops to **low** with a title saying the homepage
explains itself in prose but not in its headings. Three real sites — a database engine, an
operating system and a JavaScript library — each drew the only high-severity finding in their
report from a missing H1, on a first screen opening "X is a C-language library that implements
a small, fast … SQL database engine".

Recognise a CTA by *shape*, not by a phrase list: a link opening with an imperative verb
("Browse the range", "Speak to the team"), or one styled as a button. Count `<button>` elements
as well as links — a homepage whose action is a `<button>Get a quote</button>` was told at high
severity that no call to action was found anywhere on it.

**Say where the call to action was found, not why it was not found.** Position is measured
against the body copy — the main region with the menu, header and footer taken out — so a call
to action living in the menu is never in it. Report that: the action is standing site furniture
rather than something this page says. Where the label is in neither the copy nor the menu, the
only claim available is that its position could not be measured. A menu item reading `Download
overview`, ordinary anchor text inside a labelled `<nav>`, was reported as sitting "in markup a
text-only reader never sees".

---

## 2. Primary navigation

At least three top-level items with labels naming real destinations. Under three is **high** —
there is nowhere to go.

There is deliberately **no upper bound**: a twenty-nine-item megamenu is normal on a retail site
and costs a machine nothing to read.

Catch-all labels ("More", "Menu", "Misc", "Resources", "Info") are **medium**, and only when
*most* of the menu is made of them. One vague label beside six specific ones is not a defect:
`Products / Solutions / Customers / Resources / Pricing / Company / Contact` is ordinary correct
structure, and it used to produce a finding whose evidence listed seven destinations then said
four of them named no destination. `Products`, `Services`, `Solutions` and `Company` are section
names, off the list.

**Hidden is not absent.** If every navigation link sits inside an element marked
`aria-hidden="true"` or `hidden`, the menu exists and a reader without JavaScript is never shown
it. That is **high**, and a different finding with a different fix: expose what is there, do not
write it again. A university department's whole navigation — thirty real destination links —
lives in a `role="dialog"` panel, and "the primary navigation has 0 item(s), labels found: none"
with advice to "make sure the navigation is real HTML links" was wrong on both counts.
`role=menu` and `role=menubar` are navigation containers too.

**And where the menu leads, which is a separate question.** `homepage-links-into-the-site`
counts the addresses on this site that a machine can reach from the front page at all —
wherever on the page the link sits. A client-rendered storefront was audited whose
delivered homepage carries two `href`s, both to a font host, plus one `href=""`; the report
named the shell and never named the consequence, which is the single most actionable
sentence available about that site. A machine arriving at the front door and
following links reads one page and stops, and the nine pages that run read all came from the
sitemap against 5,000 URLs listed in it.

What counts is narrow, because the question is what a machine can fetch: another host is not a
page of this site, `href="#"` and a `javascript:` href resolve to nothing, `href=""` resolves
to the page the reader is already on, and a link whose real destination sits in its fragment —
the email-obfuscation shape — leads somewhere other than the path it names. It fires only at
zero, it is silent on a site that has no second page to link to, and it declines outright where
the header-and-footer fingerprint holds destinations the anchor loop did not, because that
disagreement is about this audit rather than about the site. It carries `unreachable-from-homepage`
rather than `dead-end`: a crawler discovering nothing at the front door is a different defect from a
visitor having nowhere to go next, it stays true on a site with no dead end anywhere, and the rule
that holds an absence claim back on a site most of which was not read is keyed on the cause — this
claim is about one document that *was* read. On a shell site the composer defers it to the shell
finding with `follows_from`, so it names the consequence without competing with its own cause. It is the one check in this skill
that judges the homepage whether or not the homepage is a JavaScript stub: every other check
here asks what a page lacks, which a stub answers no to for one reason, and this one asks what
the delivered document offers a machine — for which a stub's answer is the finding.

---

## 3. Dead ends

A non-home page with **no** internal link at all in its main content, *and* no call to action,
*and* no form is a cul-de-sac. **Medium**. That means no way onward at all, not few ways onward:
a landing page carrying one strong CTA and an enquiry page whose whole purpose is its form are
both fine. Fixing the template fixes every page using it.

**A link out of the main region counts wherever it leads.** A hosted form, a booking system or a
donation platform is a next step even though it is somebody else's host, and the test counted
internal main links only — so a page whose whole purpose is an embedded third-party form, which
ships as a container plus a link to the hosted copy, read as a page offering nowhere to go. That
link is in the delivered HTML and needs no browser to read.

**Say which links the count left out.** The crawl drops a main-region link whose destination is
held only in its fragment, since it leads to an address rather than the path it names — so a page
whose one link is an obfuscated-email anchor counts zero onward links, correctly. Name that link
in the evidence anyway: a reader opening the page sees a link, and a report saying there is none
is one they stop believing. A call to action is not filtered the same way — an anchor opening a
mail client is still something to do.

**Where the widget leaves no link at all, say so rather than guessing.** The script inserts the
form and the only mention of the service is a footer credit or nothing. If no browser read the
page *and* the page names a third party it both loads a script from and sends the visitor to,
report the page as not judged, with the host named. The pairing is what makes it evidence: an
analytics host is loaded and never linked, a social profile linked and never loaded. Every other
page keeps its finding, and the evidence says the next step was looked for in the delivered HTML
and nowhere else. The same rule governs "no call to action was found anywhere in the page", the
highest-severity sentence this skill writes.

---

## 4. Does the landing page answer the question that brought someone there?

Checks 1 to 3 judge a visitor who arrived at the homepage and browsed, and that is not the visit
this skill exists for. Establish the class of page first, then what that class has to answer.

A page joins the for-sale class only where it says so itself — the crawl typed it a product page,
or it publishes product markup about itself — and carries a price, and is not a listing. A grid of
twelve prices answers a browsing visitor; an essay quoting a competitor's fee is not a page anybody
buys from.

What such a page must add to the price is a **stock state**, a **delivery promise** and a **returns
policy**, each read three ways:

- the page's own commerce markup in JSON-LD, Microdata or RDFa;
- the paths of its own links, so a link to `/policies/refund-policy` counts;
- and, on an English site, its own words.

Answering none of the three is **medium**, and only where half or more of the priced pages are the
same, because a finding here says the template is wrong. Not an English check — a currency figure
and `availability` are structured facts — and where it cannot read at all (prose in another
language *and* no commerce markup) those pages are **set aside and counted**, not reported.

---

## 5. Is the answer in the first screen?

The same class of page, and the case study's own symptom: the facts are on the page, under several
screens of brand copy. Measured as the offset of the first currency figure in the page's own body
copy against the same 1,500 characters check 1 uses for a call to action. **Medium**, half or more
of the priced pages.

A character count needs no language and document order is the reading order on a phone — what makes
this measurable, and also its limit, so the finding says so.

---

## 6. Is there a next step that belongs to this page?

Check 3 asks whether a page offers any way onward, and almost every real page does: a menu, a
footer, a logo. Following the menu restarts a search the visitor had already finished.

So every internal link on a deep page — body, menu and footer alike — is compared against the links
four fifths of the crawled pages carry, the same share check 9 uses to separate site furniture from
a section menu. A page whose link set survives that subtraction empty offers the arriving visitor
what it offers everybody. **Medium**, half or more of the deep pages, and `dead-end` rather than a
new root cause: it is that defect one level in. A section trail counts wherever the template puts it.

---

## 7. Orphan pages

URLs the sitemap advertises that no crawled page links to. **Medium**.

Judge only pages actually visited: a sitemap URL never fetched may be linked from a page outside the
crawl budget, and guessing there would be a false positive.

---

## 8. Broken internal links

Reuse statuses the crawl already collected, then HEAD up to 40 more — but only where HEAD means
anything on this site. Three of eight major commercial sites we measured answer 403 to HEAD and 200
to GET, so the crawl establishes once per site whether HEAD is honoured, and where it is not every
unreached target is reported as *unchecked* rather than broken.

HEAD support is per-URL as well as per-site, so a HEAD that comes back dead is re-asked with a GET
before anything is reported: a cheap probe may say "alive" on its own and may never say "dead" on
its own. Only 404 and 410 count as gone. Over 20% broken is **high**, below that **medium**. At that
rate it is a maintenance failure, not an accident.

**A link whose destination is carried in its fragment is not a link to the bare path**, and no
request is spent on one. The crawl records the fragment beside the URL it normalised, so this needs
no knowledge of any scheme. Email obfuscation is the common case: the address is an encoded payload
after the `#`, the anchor becomes a `mailto:` once a script runs, and the handler path left behind
answers 404 to anybody asking for it alone. One such anchor sat on 16 pages of one site, reported as
broken on every one.

A second reading covers schemes keeping the address outside the snapshot: an anchor whose whole label
is an email address while its href is a page path. A target set aside either way is counted as
*unchecked* with the reason stated, and only a URL no anchor anywhere links plainly is set aside —
`/docs#install` does not stop `/docs` being probed.

---

## 9. Context retention

Chrome consistency is judged **once per language edition**: a German page's menu points at German
URLs and shares not one path with the English menu, so comparing them raw reported a complete,
translated navigation as absent. An edition is only recognised on evidence — the page declares that
language, or the crawl contains more than one language-shaped prefix — so a monolingual site is
judged exactly as before.

**Breadcrumbs** on deep pages (product, article, location, service, comparison) — **medium** when
half or more lack one. Someone arriving from an answer lands deep with no history; a breadcrumb is
the cheapest way to show them what section they are in. *This is the visible trail.
`structured-data-audit` owns the `BreadcrumbList` markup; the two are different fixes and both are
worth having.*

**Title–body drift**: under a third of a title's own content words appearing anywhere in its body
text or headings. **Medium**. The title was the promise that earned the click. Two kinds of page are
set aside and counted, not judged:

- **Listing pages.** A title naming a category, on the page listing it, is delivered by the item
  names — a new-arrivals grid was told it was missing the two words it lists.
- **Pages whose script spells a syllable per character.** The six-character prefix that lets an
  inflected form count trims nothing where six characters exceed a whole word, so the test becomes an
  exact match on the surface form and fails wherever grammar adds a syllable to a word's end — one
  tagline was called undelivered with three of its stems in the first paragraph. Read the script from
  the **body**: `dominant_script` names none under 20 letters, so a title-side gate passes every short
  non-Latin title.

**Too many top-level headings**: a page carrying ten or more *distinct* `<h1>` strings.
**Medium**. One homepage carried fifteen, and nothing in the marketplace said a word:
the only heading check that existed reports a page with **zero** H1s, and its own comment records
why it stops there — reporting more than one fired it on 27 of 29 real sites, because two or three
are ordinary HTML5 sectioning and stop no machine reading the page. Fifteen is not sectioning.
Fifteen tells a machine that fifteen different things are the subject of the page, which is the
same defect as none at all seen from the other side.

`H1_FLOOD = 10` is **chosen, not measured**, and the reason there is no measurement is the number
itself: this repository's six fixture sites carry at most one H1 on each of their 42 pages, and
the corpus the zero-H1 check was tuned against measured only how often a page carries more than
one. What 10 is chosen against is the shape either side of it — below it sit a sectioned article,
a theme shipping a screen-reader-only H1 beside a visible one, and a slider repeating its title;
above it sits a template using `<h1>` as a card style. Counting **distinct** strings across both
readings of the page's headings is what makes the first two count once. *`heading-hierarchy` in
`fact-extractability-audit` owns the page with no H1; this one owns the page with too many, and
they cannot both fire on one page.*

**Consistent site chrome**: pages missing the header/footer navigation the rest of the site carries.
Compare against the most common **non-empty** navigation signature — take the plain majority and the
check bails out on exactly the worst case, a site where most pages have no navigation at all.

---

## 10. Friction

Report only extremes, and say so: over 2 MB of HTML (inline script and CSS included) or more than 15
third-party script hosts is **medium**. Both numbers came down from 3 MB and 25 because at those
values nothing could ever trip them — across 302 pages on 13 real sites the heaviest page was 1.26 MB
and the worst loaded scripts from ten third-party hosts, so the old thresholds sat above anything the
web actually does. Ordinary page weight is measured but not reported.

An open modal or overlay in the delivered markup, or autoplaying video, is **medium**. **A cookie
banner alone is not a defect** — most sites are legally required to show one; only flag it if it
covers the H1 and first paragraph. Enquiry forms requiring more than eight fields are **low**.

**Count forms, not `<form>` elements.** A form the site-wide template repeats is one form and one
fix. A report once said "none of the 83 enquiry forms found requires more than 8 fields" about a site
whose only form is a newsletter box in the footer, counted once for each page the footer appears on.
Two `<form>` records are the same form when they have the same shape — the same action, method, field
count, required count and first field name — and either post to the same named address, or appear on
nearly every page built from the same chrome signature. A form with no `action` posts back to its own
page, so its shape alone never folds two pages together.

**An enquiry form has at least two fields.** One field is a subscription, a lookup or a
postcode-to-branch box: there is no question a visitor can ask through a single email input. The
newsletter word-match is a second, weaker reading and misses boxes that carry no matching text around
them, so the field count is checked as well.

---

## 11. Skimmability

Two or more paragraphs over 200 words, or a 700+ word page with a subheading only every 300 words, is
**low**. Visitors scan before they read; a page with no visual entry points gives them nothing to scan.

Three exemptions:

- **A listing page is not a wall of text.** A blog index's post titles *are* its headings, and the rule
  fired on one advising "add a subheading every 3 to 5 paragraphs".
- **Nor is a directory of links.** A catalogue of 240 anchors, no `<p>` and one subheading, was reported
  as 1,015 words too dense to skim, every "word" a link label. Over half a page's words inside list or
  table cells says this, and so does over half of them being link labels — the second test is what sees
  a catalogue of bare anchors and line breaks, which uses no list markup and scores zero on the first.
- **A script that puts no space between one word and the next has no word count.** 4,200 characters of
  Japanese measured as one word against a 700-word threshold, and the check then printed a pass it had
  not measured. Say so instead.

---

## 12. Mobile viewport

Missing on every page is **medium**, on some pages **low**. Most people arriving from an assistant are
on a phone.

---

## 13. Newsletter capture (mechanism F)

Record whether a signup exists. This is **never a finding** — it feeds a proactive recommendation about
text-first email design, because inbox summaries are built from readable text and substance carried in
an image disappears from them.

---

## Why the not-applicable floors sit where they do

Each floor in `SKILL.md`'s "Not applicable when" list is a sample-size rule, and each one has a report
behind it.

- **Fewer than four pages crawled.** Too few to establish what the site's normal navigation looks like.
- **Fewer than five sitemap URLs.** Too few to identify orphans reliably.
- **Fewer than three deep pages.** Breadcrumbs would not change how the site is navigated, and half or
  more already having one is reported as a pass with the count. The same floor governs the three
  landing-page checks: "1 of 1 product pages" is a sentence about whichever page the crawl happened to
  reach, not about a template.
- **Fewer than three crawled pages publish a price and are not listings.** Nothing to ask the
  purchase-fact questions of. A price stated only in markup has no position in the copy.
- **Fewer than eight content pages**, for the page-specific-links check.
- **Fewer than three pages comparable for title–body drift.** A page needs a title, 400+ characters of
  body text, prose of its own rather than a list of other pages, and a writing system this comparison
  runs on. The reason counts what each of those removed.
- **The homepage was crawled and then set aside.** A page delivering an empty framework mount point that
  no browser opened has not been read, and "the homepage was not crawled" is a false statement about the
  crawl standing in for a true one about the page. That the delivered HTML holds nothing readable is
  `js-shell` in `render-readability-audit`, not repeated here.
- **No extra requests could be made.** The reason says **which** of the two causes applies. The
  orchestrator appends `--no-network` both when the caller asks for it and when the run has spent its
  wall clock, so the flag alone cannot tell a choice from an exhaustion. A run handed less than 30
  seconds of budget reports a spent clock and names what used it — usually a `Crawl-delay` the site
  asked for and this audit honoured — rather than blaming a flag the caller may never have passed.
- **Page weight and third-party script counts within normal ranges.** The reason states the thresholds
  and that ordinary weight was measured but deliberately not reported.
