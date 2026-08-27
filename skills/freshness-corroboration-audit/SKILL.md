---
name: freshness-corroboration-audit
description: Check whether a brand's facts are current, agreed upon across independent sources, and distinguishable from other things sharing its name. Audits content staleness and missing date signals, counts how many authoritative off-site profiles corroborate the brand, queries Wikidata for name collisions, and detects where the site contradicts itself on telephone, address or boilerplate. Use when an AI assistant describes a brand with outdated prices, an old logo or a discontinued product, when it confuses the brand with a different company of the same name, or when a rebrand has not been picked up anywhere.
license: MIT
compatibility: Requires Python 3.10+ with requests. Makes up to 4 extra read-only requests to Wikidata's public search API; pass --no-network to make none.
allowed-tools: Bash(python3:*) Bash(python:*) Read WebSearch
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "D"
  gate: "trusted"
---

# Freshness and corroboration audit (mechanism D: fresh, agreed-upon, unambiguous)

## When to use

Run after extractability. The previous gates ask whether a fact can be reached, read and
lifted. This one asks whether it will be **believed**. Machines treat a fact as trustworthy
when independent sources agree on it, and prefer the more recent of two sources that
disagree. A brand whose new facts exist only on its own site, while directories and cached
pages still repeat the old ones, loses that comparison every time.

Reach for it on its own after a rebrand, a price change, or a move — or when an assistant
confuses the brand with something else that shares its name.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- `--now YYYY-MM-DD` fixes the reference date so a run is reproducible. Defaults to today,
  UTC. **Always pass it in tests**, or "18 months old" changes meaning as time passes.
- `--no-network` skips the Wikidata lookup.

## Procedure

### Freshness

1. **Content staleness**, only where three or more article pages were crawled. Compute each
   article's newest date signal. Report **medium** when over 70% are older than 18 months
   *and* the newest is over 12 months old. Both conditions matter: an active blog with a deep
   archive is healthy, and only the second condition distinguishes it from an abandoned one.
   18 months is where "recent" stops being defensible.

2. **Missing date signals**, which is a different problem from an old date. An article with
   no `<time>`, no `datePublished`/`dateModified` and no visible date in its text gives a
   consumer no way to judge currency at all. **Medium**. Do not expect dates on evergreen
   pages like pricing or contact.

3. **Footer copyright year.** More than one year behind the reference date is **low**. It is
   the cheapest liveness signal on a site, and a stale one colours how everything else is
   weighed.

4. **Stale year references in copy.** "As of 2023", "last updated 2022", "prices for 2021" —
   two or more years behind is **low**. Match only phrases that assert the copy is *current*
   as of a year. A bare "in 2019" is usually a founding date, and matching it would turn
   every about page into a false positive.

5. **Sitemap `lastmod` distribution.** Report the newest and the median. Nothing modified in
   over a year is **low**: `lastmod` is what crawlers use to decide re-fetch frequency, so a
   frozen site is re-read rarely and any change you *do* make takes longer to be noticed.

### Corroboration

6. **Authoritative profiles.** Collect every off-site profile linked from the pages or listed
   in `sameAs`, and count how many are authoritative: LinkedIn, Wikipedia, Wikidata,
   Crunchbase, GitHub, Google Business, Trustpilot, Yelp, Glassdoor. Fewer than three is
   **medium**; zero is **medium** with stronger evidence. A fact stated only on the brand's
   own site is one source's word for it.

7. **Entity ambiguity.** Query Wikidata's public search API once for the brand name and count
   matching entities. Two or more, *and* no disambiguation on the site, is **medium** —
   **high** at four or more. Treat the site as already disambiguated if it links to Wikidata
   or Wikipedia, or if it declares an `alternateName` plus a description plus three or more
   profiles. Where the agent runtime provides web search, record the suggested query in the
   report; record the query, not results scraped without attribution.

8. **Self-contradiction.** A brand that disagrees with itself is the hardest case for
   anything weighing sources, and does more damage than silence because it undermines every
   other fact on the site. Report **high** when:
   - an `Organization` telephone does not match the number shown on the same page;
   - an `Organization` postal code does not match the one shown on the same page;
   - more than one distinct `Organization` description is published across the site — the
     boilerplate is not one sentence but several.

   **Scope note:** JSON-LD *name* and *price* mismatches belong to `structured-data-audit`.
   This skill owns telephone, postal address and the description boilerplate. The two do not
   overlap.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (at most 4), and signals `authoritative_profile_count`,
`profile_platforms`, `has_wikidata_or_wikipedia`, `wikidata_match_count`,
`wikidata_matches`, `no_date_signals`, `newest_article_date`, `press_page_present`,
`distinct_org_descriptions`, `suggested_web_search`, `reference_date`.

All findings carry mechanism `D` and a `root_cause` of `stale-content`, `no-date-signal`,
`weak-corroboration`, `entity-ambiguity` or `nap-inconsistency`.

The headline fix is a **canonical boilerplate block** — one paragraph of identity, address
and contact details to reuse verbatim on the site, LinkedIn, the press kit, app stores and
every directory. Identical wording across independent sources is the strongest agreement
signal available and costs nothing but discipline.

## Not applicable when

- Fewer than three article pages were crawled — there is no publishing cadence to assess.
- No article carries a parsable date — reported as a missing date signal instead, not as
  staleness. The two have different fixes.
- No article or press pages exist — dates are not expected on evergreen pages.
- No copyright year appears anywhere in the text.
- Fewer than five sitemap entries carry a parsable `lastmod`.
- Three or more authoritative profiles are already linked — the reason names them.
- Wikidata returns fewer than two matches, or the site already disambiguates itself.
- `--no-network` was passed, or Wikidata could not be reached: an audit-environment
  limitation, recorded as such and never as a site defect.

## References

- `references/entity-disambiguation.md` — what makes two same-named entities separable, which
  markers matter most, and how to create a Wikidata item that holds.
