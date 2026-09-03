---
name: engagement-audit
description: Check whether a visitor who arrives mid-journey from an AI answer actually stays. Audits homepage orientation and calls to action, primary navigation, dead-end pages with no next step, orphan pages, broken internal links, breadcrumbs, title-body drift, missing site chrome, extreme page weight, intrusive interstitials and autoplay, over-long enquiry forms, mobile viewport and skimmability. Use when analytics show high bounce on AI or search referrals, when traffic arrives but never converts, or as the on-site half of a full AI-readiness audit. This covers what happens after the click, which the discoverability gates do not.
license: MIT
compatibility: Requires Python 3.10+ with requests, beautifulsoup4 and lxml. Makes up to 40 extra read-only HEAD requests to test internal links; pass --no-network to make none.
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

1. **Homepage orientation.** An H1 that is present, specific, and not "Welcome" / "Home" /
   a two-word brand name; and a call to action within the first 1,500 characters of body
   text — roughly the first screen. A CTA 4,000 characters down is not orientation.
   Missing H1 or no CTA anywhere is **high**; a late or vague one is **medium**.
   Recognise a CTA by *shape*, not by a phrase list: a link opening with an imperative verb
   ("Browse the range", "Speak to the team"), or one styled as a button.

2. **Primary navigation.** Three to nine top-level items with labels naming real
   destinations. Under three is **high** — there is nowhere to go. Over nine stops being a
   menu. Catch-all labels ("Products", "Solutions", "More", "Resources") that name no
   specific destination are **medium**.

   **Hidden is not absent.** If every navigation link on the page sits inside an element
   marked `aria-hidden="true"` or `hidden`, the menu exists and a reader without JavaScript is
   simply never shown it. That is **high**, and it is a different finding with a different
   fix: expose what is there, do not write it again. A university department's entire
   navigation - thirty real destination links - lives in a `role="dialog"` panel, and reporting
   "the primary navigation has 0 item(s), labels found: none" with advice to "make sure the
   navigation is real HTML links" was wrong on both counts. `role=menu` and `role=menubar` are
   navigation containers too.

3. **Dead ends.** A non-home page with fewer than three internal links in its main content
   *and* no call to action is a cul-de-sac. **Medium**. Fixing the template fixes every page
   using it.

4. **Orphan pages.** URLs the sitemap advertises that no crawled page links to. **Medium**.
   Judge only pages actually visited: a sitemap URL never fetched might well be linked from a
   page outside the crawl budget, and guessing would be a false positive.

5. **Broken internal links.** Reuse statuses the crawl already collected, then HEAD up to 40
   more - but only where HEAD means anything on this site. Three of eight major commercial
   sites we measured answer 403 to HEAD and 200 to GET, so the crawl establishes once per site
   whether HEAD is honoured, and where it is not every unreached target is reported as
   *unchecked* rather than broken. Only 404 and 410 count as gone. Over 20% broken is **high**, below that **medium**. At that rate it is a maintenance
   failure, not an accident.

6. **Context retention.** Chrome consistency is judged **once per language edition**: a
   German page's menu points at German URLs and shares not one path with the English menu, so
   comparing them raw reported a complete, translated navigation as absent. An edition is only
   recognised on evidence - the page declares that language, or the crawl contains more than
   one language-shaped prefix - so a monolingual site is judged exactly as before.
   - **Breadcrumbs** on deep pages (product, article, location, service, comparison) —
     **medium** when half or more lack one. Someone arriving from an answer lands deep with
     no history; a breadcrumb is the cheapest way to show them what section they are in.
     *This is the visible trail. `structured-data-audit` owns the `BreadcrumbList` markup;
     the two are different fixes and both are worth having.*
   - **Title–body drift**: under a third of a title's own content words appearing anywhere in
     its body text. **Medium**. The title was the promise that earned the click.
   - **Consistent site chrome**: pages missing the header/footer navigation the rest of the
     site carries. Compare against the most common **non-empty** navigation signature — take
     the plain majority and the check bails out on exactly the worst case, a site where most
     pages have no navigation at all.

7. **Friction.** Report only extremes, and say so: over 3 MB of inline payload or more than
   25 third-party script hosts is **medium**. Ordinary page weight is measured but not
   reported. An open modal or overlay in the delivered markup, or autoplaying video, is
   **medium**. **A cookie banner alone is not a defect** — most sites are legally required to
   show one; only flag it if it covers the H1 and first paragraph. Enquiry forms requiring
   more than eight fields are **low**.

8. **Skimmability.** Two or more paragraphs over 200 words, or a 700+ word page with a
   subheading only every 300 words, is **low**. Visitors scan before they read; a page with
   no visual entry points gives them nothing to scan.

9. **Mobile viewport.** Missing on every page is **medium**, on some pages **low**. Most
   people arriving from an assistant are on a phone.

10. **Newsletter capture (mechanism F).** Record whether a signup exists. This is **never a
    finding** — it feeds a proactive recommendation about text-first email design, because
    inbox summaries are built from readable text and substance carried in an image disappears
    from them.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (at most 40), and signals `newsletter_signup`, `has_site_search`,
`nav_item_count`.

Findings carry mechanism `G` and a `root_cause` of `no-orientation`, `dead-end`,
`orphan-pages`, `broken-links`, `no-breadcrumbs`, `title-body-drift`, `page-weight`,
`intrusive-interstitial`, `form-friction` or `readability`.

## Not applicable when

- **The site is not in English**, for the wording half of homepage orientation. Whether a heading says nothing ("welcome", "home") and whether a link reads as a call to action are both English tests. On a non-English site this check reports only whether a heading exists, and states what it did not judge. Everything else in this skill is structural.

- No content page returned HTTP 200.
- The homepage was not crawled — orientation cannot be assessed.
- Fewer than four pages were crawled — too few to establish what the site's normal
  navigation looks like.
- Fewer than five sitemap URLs — too few to identify orphans reliably.
- Fewer than three deep pages — breadcrumbs would not change how the site is navigated, and
  half or more already having one is reported as a pass with the count.
- Fewer than three pages have both a title and 400+ characters of body text.
- No page carries header or footer navigation at all — the navigation finding covers that
  rather than this one.
- `--no-network` was passed — link probes are skipped and said so.
- Page weight and third-party script counts are within normal ranges: the reason states the
  thresholds and that ordinary weight was measured but deliberately not reported.

## References

- `references/engagement-heuristics.md` — every threshold, why it sits where it does, and
  what a visitor arriving from an AI answer needs that an ordinary visitor does not.
