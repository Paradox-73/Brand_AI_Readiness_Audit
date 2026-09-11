---
name: engagement-audit
description: Check whether a visitor who arrives mid-journey from an AI answer actually stays. Audits the page a deep link actually lands on - whether it carries the facts a visitor arriving with intent needs (price, stock, delivery, returns), whether they are in the first screen or under several screens of brand copy, and whether it offers a next step of its own rather than the menu every page carries - as well as homepage orientation and calls to action, primary navigation, dead-end pages with no next step, orphan pages, broken internal links, breadcrumbs, title-body drift, missing site chrome, extreme page weight, intrusive interstitials and autoplay, over-long enquiry forms, mobile viewport and skimmability. Use when analytics show high bounce on AI or search referrals, when traffic arrives but never converts, or as the on-site half of a full AI-readiness audit. This covers what happens after the click, which the discoverability gates do not.
license: MIT
compatibility: Requires Python 3.10+ with requests, beautifulsoup4 and lxml. Makes up to 40 extra read-only requests to test internal links — HEAD to ask, and a GET to confirm any target HEAD reported as dead. A link target robots.txt disallows is never probed. Pass --no-network to make none.
allowed-tools: Bash(python3:*) Bash(python:*) Read
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "G F"
  gate: "visitor stays"
---

# Engagement audit (mechanism G: does an arriving visitor stay?)

## When to use

Run last. Every other skill in this marketplace works on getting a machine to find, read and
quote the brand. This one starts the moment that works and someone clicks through.

A visitor sent by an assistant is unlike a visitor from anywhere else: they arrive
**mid-journey**, already knowing roughly what they want, often landing deep in the site with
no navigation history behind them — on a page that has no idea any of that happened. That
mismatch is mechanism G, and it is why this skill exists as its own concern rather than being
folded into the discoverability gates.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- `--no-network` to skip the internal-link HEAD probes.

## Procedure

Nineteen checks are registered over the snapshot; they are grouped here into thirteen
questions, in this order. Each line below is the question and the worst severity it can reach.
Every registered check id, with what it reads, its threshold and where that number came from,
every exemption and the real report each exemption was written against, is in
`references/engagement-checks.md`.

1. **Homepage orientation.** An H1 that says something, and a call to action inside the first
   1,500 characters of body copy. No call to action anywhere is **high**; a late or vague one,
   or a missing H1 alongside another problem, is **medium**. A missing H1 on its own belongs
   to `fact-extractability-audit` and is not reported twice.
2. **Primary navigation.** Three or more top-level items naming real destinations. Under
   three is **high**; a menu made mostly of catch-all labels is **medium**. Navigation that
   exists but is marked `aria-hidden` or `hidden` is its own **high** finding with its own
   fix — expose what is there, do not write it again. And separately, how many pages of the
   site a machine can reach from the homepage at all: a front page whose only `href`s leave the
   site, resolve to itself or resolve to nothing is **high**, because a crawler entering there
   reads one page and stops. Fires only at zero, never on a one-page site, and declines where
   the header-and-footer fingerprint holds destinations the anchor loop did not.
3. **Dead ends.** A non-home page with no internal link in its main content, no call to
   action and no form. **Medium**, and a template fix.
4. **Do the landing pages carry the purchase facts?** On a page that says it is for sale and
   states a price: a stock state, a delivery promise and a returns policy, read from commerce
   markup, from its own link paths, and from its own words. None of the three on half or more
   of the priced pages is **medium**.
5. **Is the answer in the first screen?** The offset of the first currency figure in the body
   copy, against the same 1,500 characters. **Medium** on half or more of the priced pages.
6. **Is there a next step that belongs to this page?** Subtract the links four fifths of the
   crawled pages carry; a deep page left with nothing offers an arriving visitor only what it
   offers everybody. **Medium**, and `dead-end` one level in rather than a new root cause.
7. **Orphan pages.** Sitemap URLs no crawled page links to. **Medium**, judged only over
   pages actually visited.
8. **Broken internal links.** Reuse the crawl's statuses, then HEAD up to 40 more, only where
   this site answers HEAD honestly. Over 20% is **high**, below that **medium**.
9. **Context retention.** Breadcrumbs on deep pages, title–body drift, site chrome consistent
   with the rest of the site, and whether the top-level heading changes with the page or names
   the site on four fifths of them, and whether a page carries ten or more distinct H1s —
   fifteen subjects on one page names none. **Medium** each, judged once per language edition.
   The heading tests compare whole strings rather than words, so they need no word matching and
   run in any writing system. A page with *no* H1 belongs to `fact-extractability-audit`; two
   or three are ordinary HTML5 sectioning and are not a defect here.
10. **Friction.** Extremes only: over 2 MB of HTML or more than 15 third-party script hosts,
    an open overlay in the delivered markup, autoplaying video — **medium**. Enquiry forms
    over eight fields are **low**. A cookie banner alone is not a defect.
11. **Skimmability.** Two or more paragraphs over 200 words, or a 700+ word page subheaded
    only every 300 words. **Low**.
12. **Mobile viewport.** Missing everywhere is **medium**, on some pages **low**.
13. **Newsletter capture (mechanism F).** Recorded, never a finding: it feeds the proactive
    recommendation about text-first email design.

Four rules govern all thirteen, and are what to carry over when working through this by hand.

- **Say where you looked, and where you did not.** Position is measured against body copy
  with the chrome removed, so a call to action in the menu is standing site furniture rather
  than something this page says — report that, not an absence.
- **Count things, not elements.** One `<form>` repeated by a template is one form and one
  fix; counting elements produced "none of the 83 enquiry forms found requires more than 8
  fields" about a site whose only form is a footer newsletter box.
- **A page nobody could read is not a page with nothing on it.** Where a third-party widget
  is loaded and linked and no browser read the page, report the page as not judged with the
  host named, rather than as a page offering nowhere to go.
- **Judge per language edition.** A German menu shares no path with the English one;
  comparing them raw reported a complete translated navigation as absent.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (at most 40), and signals `newsletter_signup`, `has_site_search`,
`nav_item_count`, `link_targets_that_are_not_page_addresses`,
`pages_whose_next_step_may_be_embedded`, `landing_pages_judged_for_purchase_facts`,
`landing_pages_stating_only_a_price`, `landing_pages_that_bury_the_price` and
`deep_pages_offering_only_site_wide_links`.

The last four are measurements rather than the presence of a page type: three of the report's
fourteen proactive recommendations fired on six sites out of six because they were gated on a
site having product pages at all.

Findings carry mechanism `G` and a `root_cause` of `no-orientation`, `dead-end`,
`unreachable-from-homepage`, `orphan-pages`, `broken-links`, `no-breadcrumbs`,
`inconsistent-chrome`, `title-body-drift`, `page-weight`, `intrusive-interstitial`,
`form-friction` or `readability`.

A finding here that says something is **missing** carries `checked[]`, the independent sources
it consulted and found empty. One source caps it at medium confidence and the report says so.
Working through this skill by hand, do the same: say where you looked, and look in a second
place before stating at high confidence that something is not there. Every false positive this
marketplace has made on a real site was one detector with a finite list of shapes, reported as
a fact about the site.

## Not applicable when

- **The site is not in English**, for the wording half of homepage orientation. Whether a
  heading says nothing and whether a link reads as a call to action are both English tests. On
  a non-English site this check reports only whether a heading exists, and states what it did
  not judge. `landing-page-answers` gates its prose reading the same way and sets the affected
  pages aside by name.
- Two checks read English word lists and **do not** decline: `primary-navigation`'s
  control-label and catch-all-label vocabularies, and the call-to-action half of
  `dead-end-pages`. Both have language-neutral routes to the same answer — navigation is
  counted from `<nav>` links whatever they say, and a dead end is settled by the main-content
  link count, the form count, the off-site link and an add-to-cart control matched on the
  identifiers a developer types rather than on the shop's copy. What is lost is that
  `primary-navigation` cannot report a catch-all menu on a non-English site, and this is
  stated here because the report does not state it.
- **The site's prose is in a language these patterns are not written for, and the page carries
  no commerce markup** — one reading standing out of three, so those pages are set aside and
  counted in the evidence rather than reported. The price and first-screen measurements are
  language-neutral and still run.
- No content page returned HTTP 200.
- The homepage was not crawled, or was crawled and then set aside as unread.
- The sample is below the floor for a given check: four pages for navigation, five sitemap
  URLs for orphans, three deep pages for breadcrumbs and the landing-page checks, three priced
  non-listing pages for the purchase facts, eight content pages for page-specific links, three
  comparable pages for title–body drift.
- No page carries header or footer navigation at all — the navigation finding covers that
  rather than this one.
- No extra requests could be made — link probes are skipped and the reason says **which** of
  the two causes applies, a caller's `--no-network` or a spent wall clock.
- Page weight and third-party script counts are within normal ranges: the reason states the
  thresholds and that ordinary weight was measured but deliberately not reported.

`references/engagement-checks.md` gives the report behind each floor, and why a floor rather
than a threshold is the right shape for every one of them.

## References

- `references/engagement-checks.md` — every one of the nineteen registered check ids: what
  each measures, its threshold and where that number came from, the severity it can reach,
  every exemption, and the real report each exemption was written against.
- `references/engagement-heuristics.md` — every threshold, why it sits where it does, and
  what a visitor arriving from an AI answer needs that an ordinary visitor does not.
