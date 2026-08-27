# Engagement heuristics

Every threshold in `engagement-audit`, why it sits where it does, and what a visitor arriving
from an AI answer needs that an ordinary visitor does not.

---

## The visitor this skill is about

Someone sent by an assistant is not a normal visitor:

- They arrive **mid-journey**. The explaining has already been done for them, elsewhere.
- They arrive **with specific intent**. They are checking whether this is the right place,
  not browsing to find out what it is.
- They arrive **deep**, often not on the homepage, with no navigation history behind them.
- They arrive **carrying context the site cannot see**. The site does not know which question
  was asked, so it greets them exactly as it greets everyone.

That last point is the root cause of the bounce, and it is mechanism G. The fixes are
orientation, a next step, and a way back.

---

## Orientation

| Check | Threshold | Why |
|---|---|---|
| H1 present and specific | not in the generic set; 3+ words | "Welcome", "Home", "Hello" identify nothing. A two-word brand name is a label, not a description. |
| Primary CTA position | within the first **1,500 characters** of body text | Roughly the first screen. A CTA 4,000 characters down is not orientation, it is a footer. |
| Navigation size | **3 to 9** top-level items | Under 3 gives nowhere to go. Over 9 stops being a menu and becomes a list nobody reads. |
| Navigation labels | not `Products`, `Solutions`, `More`, `Resources`, `Company`, `Info` alone | These name no destination. "Products" on a site with six ranges tells a visitor nothing about which one is theirs. |

### CTAs are recognised by shape, not by phrase list

The first version matched a fixed list of marketing phrases and missed most real CTAs —
"Browse the ironmongery range" and "Speak to the trade counter" are plainly calls to action
and matched nothing.

A link is now a CTA if **its first word is an imperative verb** (get, start, book, buy, shop,
browse, view, see, request, contact, call, order, try, download, subscribe, join, apply,
explore, discover, find, learn, read, watch, schedule, reserve, sign, register, talk, speak,
ask, enquire, compare, choose, select, plan, build, create, send, email, visit, check, claim,
take, open, configure, estimate, quote, hire, arrange…) **or** it carries button markup
(`btn`, `button`, `cta`, `call-to-action`, `primary-action` in its class, id or role).

This was a false positive found by running the clean fixture, and it is a good example of why
`good-site` exists.

---

## Dead ends and wayfinding

| Check | Threshold | Why |
|---|---|---|
| Dead-end page | under **3** internal links in main content **and** no CTA | Both conditions required. A page with a strong CTA and few links is a landing page, not a cul-de-sac. |
| Orphan pages | in the sitemap, crawled, linked from no crawled page | Only pages actually visited are judged. A sitemap URL never fetched might well be linked from a page outside the crawl budget, and guessing would be a false positive. |
| Broken internal links | any 4xx/5xx; **over 20%** is high | Reuses statuses the crawl already has, then probes up to 20 more with HEAD. Above a fifth it is a maintenance failure, not an accident. |
| Breadcrumbs on deep pages | **half or more** missing | Deep types only: product, article, location, service, comparison. A breadcrumb on a homepage is meaningless. |
| Site chrome | pages sharing few or none of the site's usual nav links | Compared against the most common **non-empty** navigation signature — see below. |

### Why "non-empty" matters

Taking the plain majority signature meant the check bailed out on exactly the worst case: a
site where *most* pages have no navigation at all, making "no chrome" the mode and the
comparison meaningless. Found by running `dead-end-site`, where four of five pages have no
header.

A visitor who lands on a page with no navigation has no way back into the site. That page
becomes the whole experience, and when it ends, so does the visit.

---

## Title–body drift

Under **a third** of a title's content words appearing anywhere in its body text.

The title is the promise that earned the click — it is what the assistant showed and what the
visitor decided on. A visitor who does not see it confirmed in the first few lines assumes
they are in the wrong place, and the fastest way to check is to go back.

The brand suffix is stripped before comparing ("… | Brightpath Analytics" appears in every
title and would mask real drift), stopwords are removed, and matching is on the first six
characters of each token so plurals and simple inflections still count.

---

## Friction: only extremes are reported

Ordinary page weight is measured and deliberately **not** reported. Reporting every heavy
page would bury the findings that matter under numbers nobody will act on.

| Check | Threshold | Why |
|---|---|---|
| Page weight | over **3 MB** of HTML + inline script + inline CSS | Not "heavy" — extreme. Most sites never approach this. |
| Third-party scripts | over **25** distinct third-party hosts | Beyond this, first paint is not under the site's control. Marketing and analytics tags accumulate and are almost never removed. |
| Interstitials | an open modal/overlay in the delivered markup, or autoplaying video | Content covered before it can be read. |
| Enquiry forms | over **8** required fields | A visitor who arrived from an answer has invested one click, not a decision. |

### A cookie banner alone is not a defect

Most sites are legally required to show one. It is only reported if it covers the H1 and
first paragraph. Flagging every cookie banner would be the single largest source of noise in
this skill, and would be advice the site owner cannot act on.

Search and newsletter forms are excluded from the field-count check — a search box is
supposed to be one field, and a newsletter form is supposed to be short.

---

## Skimmability

| Check | Threshold | Why |
|---|---|---|
| Walls of text | **2 or more** paragraphs over 200 words | One long paragraph is a stylistic choice. Two or more is a pattern. |
| Subheading density | 700+ word page with a subheading only every 300 words | Under 700 words a page does not need internal subheadings. |
| Mobile viewport | missing | `medium` if every page, `low` if some. Most people arriving from an assistant are on a phone, and a page they must pinch to read is a page they leave. |

Visitors scan before they read. A page with no visual entry points offers nothing to scan, so
a visitor cannot tell in three seconds whether their answer is here.

---

## Boundaries with other skills

Two pairs look like overlaps and are not:

| This skill | The other skill | Why both |
|---|---|---|
| **Visible** breadcrumb trail | `structured-data-audit` checks `BreadcrumbList` markup | Different fixes, different owners. A site can have one without the other, and both are worth having. |
| **Broken internal links** (`broken-links`) | `crawl-access-audit` reports the non-200 rate (`non-200`) | One is "pages on this site fail"; the other is "pages link to things that fail". Different root causes, so deduplication correctly keeps both. |

Newsletter capture is detected but **never reported as a finding**. It feeds the
`R-EMAIL-TEXT-FIRST` proactive recommendation (mechanism F), because having a newsletter is
not a defect and the guidance only applies if one exists.
